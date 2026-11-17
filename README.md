# dian-cufe-rpa

Prueba técnica – Analista de Ciencia de Datos (2026).

- **Parte 1 (este README):** RPA que consulta facturas electrónicas por CUFE en el
  [catálogo VPFE de la DIAN](https://catalogo-vpfe.dian.gov.co/User/SearchDocument), resuelve el captcha y
  descarga el PDF de la representación gráfica.
- **Parte 2:** extracción de los datos de los PDF a DataFrames y CSV.

> Los datos de la prueba son ficticios y se usan solo para evaluación.

## Requisitos

- Python 3.10 o superior
- Una cuenta en [CapSolver](https://www.capsolver.com/) con saldo. Cada factura usa 2 tokens, unos US$0,0024.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # y escribir la API key: capsolver_key=CAP-...
```

## Uso

```bash
.venv/bin/python rpa_descarga.py                 # procesa cufes.txt con el NIT por defecto (900156264)
.venv/bin/python rpa_descarga.py --force         # vuelve a descargar aunque el PDF ya exista
.venv/bin/python rpa_descarga.py --input otros.csv --nit 123456789 --retries 5
```

**Control de costo:** antes de crear el cliente de CapSolver, el script revisa cada PDF de `output/pdfs/`:
que exista, empiece con `%PDF` y abra con el NIT. Si todos son válidos, termina sin llamar a CapSolver ni
a la DIAN y sin pedir la API key. Solo se descargan, y solo gastan tokens, los PDF faltantes o corruptos.
Si falta una copia sin clave, se regenera desde el original sin usar la red. La función
`pendientes()` de `rpa_descarga.py` hace esta revisión y la Parte 2 también puede usarla.

`cufes.txt` tiene un CUFE por línea. Se puede indicar un NIT distinto por factura con el formato `cufe,nit`.

**Salidas en `output/`:**

| Ruta | Contenido |
|---|---|
| `pdfs/<cufe>.pdf` | PDF original de la DIAN, **protegido con contraseña = NIT** consultado |
| `pdfs_sin_clave/<cufe>.pdf` | Copia descifrada, lista para extraer texto (Parte 2) |
| `metrics_rpa.csv` | Registro acumulativo por corrida (`run_id`): estado, intentos, tiempo por fase, bytes y error |

## Cómo funciona

El captcha del sitio es **Cloudflare Turnstile**: la casilla "Verifique que es un ser humano". No tiene una
imagen con texto, así que no sirve un OCR. El token lo genera **CapSolver** (tarea
`AntiTurnstileTaskProxyLess`) y se envía con el formulario. Todo el flujo se hace por HTTP
(`requests`), sin navegador, y por eso corre sin pantalla en un servidor.

```
GET  /User/SearchDocument            formulario + antiforgery token + sitekey Turnstile
     ── CapSolver: token #1
POST /User/SearchDocument            CUFE + NIT (obligatorio) + token #1
POST /Document/ShowDocumentToPublic  re-envío del formulario automático (Id, Token, Captcha, Nit)
     ── CapSolver: token #2
POST /Document/DownloadPDF           trackId + token + captcha=#2  →  application/pdf (cifrado con el NIT)
```

Código:
- `rpa/captcha.py`: cliente de CapSolver detrás de la interfaz `CaptchaSolver.solve(url, sitekey)`.
  Permite cambiar de proveedor (2Captcha, modo manual) sin tocar el resto.
- `rpa/dian.py`: `DianClient.download(cufe, nit, clock)` implementa el flujo y mide cada fase.
- `rpa_descarga.py`: CLI con reintentos (espera creciente), omisión de los PDFs ya descargados,
  descifrado y métricas.

### Diferencias con el enunciado

- **El NIT es obligatorio.** El sitio ya no acepta solo el CUFE: rechaza la búsqueda con
  "El NIT del emisor o receptor es requerido". Se usó el NIT **900156264**, que entregó el evaluador.
- **Los PDF vienen cifrados.** La contraseña es el NIT con el que se hizo la consulta. El RPA guarda el
  original y una copia sin clave.
- **Se necesitan dos captchas por factura:** uno para buscar y otro para descargar.

## Resultados y métricas (corrida del 17-nov-2026, `run_id 20261117-154316`)

- **10/10** PDF descargados y verificados: empiezan con `%PDF` y abren con el NIT. Pesan entre 68 y 70 KB.
- **Tiempo por factura sin incidencias:** unos **6,5–8,8 s**.

| Fase | Promedio |
|---|---|
| GET formulario | 0,2–0,3 s |
| Captcha #1 (CapSolver) | 2,2 s |
| Búsqueda + detalle | 0,35 s |
| Captcha #2 (CapSolver) | 2,3 s |
| Descarga PDF | 1,9 s |

- **Restricciones observadas:**
  - Los dos captchas son cerca del 65 % del tiempo de cada factura. El proceso depende de un
    servicio externo pago (unos US$1,20 por cada 1.000 tokens).
  - En la corrida de referencia, **3 de 10** facturas tuvieron un *timeout* de 60 s al cargar el formulario
    de la DIAN. Las tres se completaron en el segundo intento: unos 68 s en total y una media de la corrida de 25,5 s por factura.
    En otra corrida sin incidencias, la media fue 7,0 s por factura (70 s para las 10).
  - El proceso es secuencial a propósito, para no cargar el sitio de la DIAN. Los tokens Turnstile caducan
    en pocos minutos, así que se piden justo antes de usarlos.
  - El flujo depende del HTML actual del sitio (ids de formularios y nombres de campos). Si la DIAN cambia
    el formulario, hay que ajustar `rpa/dian.py`.

## Parte 2 – Extracción a CSV

```bash
.venv/bin/python extraer_facturas.py
```

Antes de empezar, revisa sin conexión (con `pendientes()` de la Parte 1) que existan los 10 PDF válidos.
Si falta alguno, pide ejecutar primero `rpa_descarga.py`. La extracción no usa la red ni CapSolver.

**Salidas en `output/`:**

| Ruta | Contenido |
|---|---|
| `csv/<cufe>.csv` | Un CSV por factura: el DataFrame de ese archivo, con una fila por producto |
| `facturas.csv` | Las 10 facturas consolidadas (21 filas) |
| `metrics_extraccion.csv` | Tiempo por archivo, número de ítems y validación del subtotal |

**Columnas:**
- `cufe`
- `numero_factura`, `fecha_emision` (ISO `AAAA-MM-DD`) y `nit_emisor`, de "Datos del Documento" y
  "Datos del Emisor/Vendedor". Se repiten en cada fila.
- `codigo`, `descripcion`, `cantidad` y `precio_unitario`, de "Detalles de Productos". Los valores
  numéricos se convierten del formato colombiano: `$ 1.234,56` pasa a `1234.56`.

### Método

El enunciado habla de "extraer el texto de las imágenes". Sin embargo, los PDF de la DIAN tienen
**capa de texto**: los genera la "Solución Gratuita DIAN" y no son escaneos. Por eso el texto se lee
directamente con **pdfplumber**, sin OCR. Este método es exacto y no tiene errores de reconocimiento.

- **Encabezado:** expresiones regulares sobre el texto de la página (`Número de Factura:`,
  `Fecha de Emisión:` y `Nit del Emisor:`).
- **Productos:** pdfplumber detecta las tablas. La de productos se identifica por sus encabezados y
  las columnas se ubican por nombre, no por posición, así que se ignora la tabla de descuentos.
- **Descripciones cortadas a mitad de palabra:** el PDF parte las descripciones largas así,
  por ejemplo "UNILATERA" + "L". Por eso cada celda se reconstruye carácter a carácter y las líneas se
  unen sin separador. Cuando el corte cae en un espacio, el PDF conserva ese espacio, de modo que
  `VIH ` + `1 Y 2` se reconstruye correctamente.
- **Validación automática:** se compara la suma de `cantidad × precio_unitario` con el **Subtotal**
  del PDF.

### Resultados y métricas

- **10/10** facturas extraídas, **21** ítems en total, y el **subtotal cuadra en las 10**.
- **Tiempo:** unos **0,27 s por factura** (entre 0,21 y 0,34 s) y **unos 2,7 s para las 10**.
  Es menos del 4 % de lo que tarda la descarga.
- **Restricciones y limitaciones:**
  - Depende del formato de la "Solución Gratuita DIAN". Un PDF de otro proveedor tecnológico o
    escaneado (sin capa de texto) necesitaría otro parser u OCR, por ejemplo Tesseract.
  - Si el PDF corta una línea justo después de un signo sin guardar el espacio, la descripción
    puede quedar pegada. Pasa una vez: `DEFICIENCIA -DISCAPACIDAD`. El texto es fiel a los
    caracteres del PDF.
  - `codigo` puede venir vacío porque así está en la factura (`HTFE-6599`).
