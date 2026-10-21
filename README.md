# Ageing por mercado

Herramienta que corre **100 % en el navegador**: cargás un Excel (`.xlsx`/`.xls`) exportado de SAP y descarga `Ageing_By_Market.xlsx` con una hoja por `Company Code`, con el ageing por bucket (Not Due, 1-30, 31-60, 61-90, 91-120, +121), separado en Customer / Intercompany (cuentas G/L que empiezan con `156`), totales y porcentajes.

Ningún dato se sube a internet: el procesamiento es local y la página funciona sin conexión.

## Uso

- **Local:** abrir `index.html` con doble clic.
- **GitHub Pages:** *Settings → Pages → Deploy from a branch → `main` / root*. La página queda en `https://<usuario>.github.io/<repo>/`. (Aun así, el archivo se procesa en tu navegador, no en GitHub.)

## Columnas requeridas (primera hoja, encabezados en la fila 1)

`Company Code`, `G/L Account`, `Amount in local currency`, `Arrears after net due date`

## Estructura

| Ruta | Descripción |
|---|---|
| `index.html` | Aplicación final, un solo archivo (generado) |
| `src/template.html` | Código fuente (HTML + CSS + JS) |
| `vendor/xlsx.full.min.js` | [SheetJS](https://sheetjs.com) 0.18.5 (Apache-2.0) |
| `build.py` | Incrusta la librería en la plantilla y genera `index.html` |

Para modificar la lógica: editar `src/template.html` y ejecutar `python build.py`.

## Aviso

El `.gitignore` excluye archivos Excel/CSV para evitar subir datos corporativos por accidente.
