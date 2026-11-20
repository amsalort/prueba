/*
 * Minimal AppImage (type 2) runtime for RetroVisor.
 *
 * The file layout is the standard one: this ELF executable followed by a
 * SquashFS image.  Bytes 8..10 of the ELF header carry the "AI\x02" magic.
 *
 * Instead of mounting the image with FUSE (which needs libfuse2/fusermount
 * and fails in many setups), the image is extracted once into
 * ~/.cache/retrovisor-appimage/<build-id>/ and AppRun is executed from there.
 * Later launches reuse the extracted copy, so they start instantly.
 *
 * Supported options (same names as the official runtime):
 *   --appimage-extract       extract to ./squashfs-root
 *   --appimage-offset        print the SquashFS offset
 *   --appimage-help          show help
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <squashfuse.h>

static off_t elf_size(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0)
        return -1;
    Elf64_Ehdr eh;
    if (read(fd, &eh, sizeof eh) != (ssize_t)sizeof eh) {
        close(fd);
        return -1;
    }
    close(fd);
    if (memcmp(eh.e_ident, ELFMAG, SELFMAG) != 0 || eh.e_ident[EI_CLASS] != ELFCLASS64)
        return -1;
    off_t sht_end = (off_t)eh.e_shoff + (off_t)eh.e_shentsize * eh.e_shnum;
    off_t pht_end = (off_t)eh.e_phoff + (off_t)eh.e_phentsize * eh.e_phnum;
    return sht_end > pht_end ? sht_end : pht_end;
}

static int mkdir_p(const char *path, mode_t mode)
{
    char tmp[PATH_MAX];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            if (mkdir(tmp, mode) < 0 && errno != EEXIST)
                return -1;
            *p = '/';
        }
    }
    if (mkdir(tmp, mode) < 0 && errno != EEXIST)
        return -1;
    return 0;
}

static int rm_rf(const char *path)
{
    struct stat st;
    if (lstat(path, &st) < 0)
        return 0;
    if (S_ISDIR(st.st_mode)) {
        DIR *d = opendir(path);
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) {
                if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
                    continue;
                char sub[PATH_MAX];
                snprintf(sub, sizeof sub, "%s/%s", path, e->d_name);
                rm_rf(sub);
            }
            closedir(d);
        }
        return rmdir(path);
    }
    return unlink(path);
}

static int extract_file(sqfs *fs, sqfs_inode *inode, const char *dest, mode_t mode)
{
    int fd = open(dest, O_WRONLY | O_CREAT | O_TRUNC, mode & 07777);
    if (fd < 0)
        return -1;
    static char buf[1 << 17];
    sqfs_off_t size = (sqfs_off_t)inode->xtra.reg.file_size, pos = 0;
    while (pos < size) {
        sqfs_off_t n = size - pos > (sqfs_off_t)sizeof buf ? (sqfs_off_t)sizeof buf : size - pos;
        if (sqfs_read_range(fs, inode, pos, &n, buf) != SQFS_OK) {
            close(fd);
            return -1;
        }
        if (write(fd, buf, (size_t)n) != (ssize_t)n) {
            close(fd);
            return -1;
        }
        pos += n;
    }
    fchmod(fd, mode & 07777);
    return close(fd);
}

static int extract_all(const char *image, off_t offset, const char *dest)
{
    sqfs fs;
    if (sqfs_open_image(&fs, image, (size_t)offset) != SQFS_OK) {
        fprintf(stderr, "No se pudo abrir la imagen SquashFS embebida.\n");
        return -1;
    }
    if (mkdir_p(dest, 0755) < 0) {
        perror(dest);
        return -1;
    }
    sqfs_traverse trv;
    sqfs_err err;
    if (sqfs_traverse_open(&trv, &fs, sqfs_inode_root(&fs)) != SQFS_OK)
        return -1;
    int rc = 0;
    while (sqfs_traverse_next(&trv, &err)) {
        if (trv.dir_end)
            continue;
        char out[PATH_MAX];
        snprintf(out, sizeof out, "%s/%s", dest, trv.path);
        sqfs_inode inode;
        if (sqfs_inode_get(&fs, &inode, sqfs_dentry_inode(&trv.entry)) != SQFS_OK) {
            rc = -1;
            break;
        }
        mode_t mode = inode.base.mode;
        if (S_ISDIR(mode)) {
            if (mkdir(out, 0755) < 0 && errno != EEXIST) { rc = -1; break; }
        } else if (S_ISREG(mode)) {
            if (extract_file(&fs, &inode, out, mode) < 0) { perror(out); rc = -1; break; }
        } else if (S_ISLNK(mode)) {
            char target[PATH_MAX];
            size_t sz = sizeof target;
            if (sqfs_readlink(&fs, &inode, target, &sz) != SQFS_OK) { rc = -1; break; }
            unlink(out);
            if (symlink(target, out) < 0) { perror(out); rc = -1; break; }
        }
    }
    if (err != SQFS_OK)
        rc = -1;
    sqfs_traverse_close(&trv);
    sqfs_destroy(&fs);
    return rc;
}

static int build_id(const char *image, off_t offset, char *out, size_t outlen)
{
    struct squashfs_super_block sb;
    int fd = open(image, O_RDONLY);
    if (fd < 0)
        return -1;
    ssize_t r = pread(fd, &sb, sizeof sb, offset);
    close(fd);
    if (r != (ssize_t)sizeof sb || sb.s_magic != 0x73717368)
        return -1;
    snprintf(out, outlen, "%08x-%llx", sb.mkfs_time, (unsigned long long)sb.bytes_used);
    return 0;
}

int main(int argc, char **argv)
{
    char self[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    if (n < 0) {
        perror("readlink");
        return 1;
    }
    self[n] = 0;

    off_t offset = elf_size(self);
    if (offset <= 0) {
        fprintf(stderr, "AppImage dañada: no se pudo calcular el desplazamiento.\n");
        return 1;
    }

    if (argc > 1 && !strcmp(argv[1], "--appimage-offset")) {
        printf("%lld\n", (long long)offset);
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--appimage-help")) {
        printf("Opciones:\n"
               "  --appimage-extract   Extrae el contenido en ./squashfs-root\n"
               "  --appimage-offset    Muestra el desplazamiento de la imagen\n"
               "  --appimage-help      Esta ayuda\n");
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--appimage-extract")) {
        return extract_all(self, offset, "squashfs-root") == 0 ? 0 : 1;
    }

    char id[64];
    if (build_id(self, offset, id, sizeof id) < 0) {
        fprintf(stderr, "AppImage dañada: SquashFS no encontrado.\n");
        return 1;
    }

    char base[PATH_MAX];
    const char *xdg = getenv("XDG_CACHE_HOME");
    const char *home = getenv("HOME");
    if (xdg && *xdg)
        snprintf(base, sizeof base, "%s/retrovisor-appimage", xdg);
    else if (home && *home)
        snprintf(base, sizeof base, "%s/.cache/retrovisor-appimage", home);
    else
        snprintf(base, sizeof base, "/tmp/retrovisor-appimage-%d", (int)getuid());

    char appdir[PATH_MAX], marker[PATH_MAX];
    snprintf(appdir, sizeof appdir, "%s/%s", base, id);
    snprintf(marker, sizeof marker, "%s/.extraido", appdir);

    if (access(marker, F_OK) != 0) {
        fprintf(stderr, "RetroVisor: preparando la aplicación (solo la primera vez)...\n");
        if (mkdir_p(base, 0755) < 0) {
            perror(base);
            return 1;
        }
        /* Remove copies of older builds. */
        DIR *d = opendir(base);
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) {
                if (e->d_name[0] == '.' || !strcmp(e->d_name, id))
                    continue;
                char old[PATH_MAX];
                snprintf(old, sizeof old, "%s/%s", base, e->d_name);
                rm_rf(old);
            }
            closedir(d);
        }
        char tmp[PATH_MAX];
        snprintf(tmp, sizeof tmp, "%s/.tmp-%d", base, (int)getpid());
        rm_rf(tmp);
        if (extract_all(self, offset, tmp) < 0) {
            rm_rf(tmp);
            fprintf(stderr, "No se pudo extraer la aplicación en %s\n", base);
            return 1;
        }
        char tmpmarker[PATH_MAX];
        snprintf(tmpmarker, sizeof tmpmarker, "%s/.extraido", tmp);
        int mfd = open(tmpmarker, O_CREAT | O_WRONLY, 0644);
        if (mfd >= 0)
            close(mfd);
        if (rename(tmp, appdir) < 0) {
            /* Another instance may have won the race. */
            rm_rf(tmp);
            if (access(marker, F_OK) != 0) {
                perror(appdir);
                return 1;
            }
        }
    }

    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof cwd))
        setenv("OWD", cwd, 1);
    setenv("APPIMAGE", self, 1);
    setenv("APPDIR", appdir, 1);
    setenv("ARGV0", argv[0], 1);

    char apprun[PATH_MAX];
    snprintf(apprun, sizeof apprun, "%s/AppRun", appdir);
    argv[0] = apprun;
    execv(apprun, argv);
    perror(apprun);
    return 1;
}
