# Altiplano al Día — Monitor de noticias de Facebook

App de escritorio (Tkinter) que revisa páginas públicas de Facebook, detecta publicaciones
nuevas, las redacta como nota periodística con IA (DeepSeek o Claude) y genera un PDF por
noticia.

## Instalación en una PC nueva (Windows 10/11, sin Python)

1. Descarga el proyecto, de una de estas dos formas:
   - con Git: `git clone https://github.com/Canazachyub/altiplano-al-dia.git`
   - sin Git: botón verde **Code → Download ZIP** en GitHub, y descomprímelo.
2. Doble clic en **`INSTALAR.bat`**. Se encarga de todo:
   - si no hay Python, descarga el instalador oficial de Python 3.12 desde python.org y lo
     instala **dentro de la carpeta del proyecto** (`python/`), sin permisos de administrador
     y sin tocar el resto del sistema;
   - crea el entorno `.venv` e instala las dependencias;
   - usa Chrome o Edge (Edge viene con Windows), así que no hace falta descargar un navegador;
   - crea un acceso directo **"Altiplano al Dia"** en el escritorio.
3. Abre la app con el acceso directo o con **`Iniciar Monitor.bat`**. Si falta la instalación,
   el lanzador ejecuta el instalador solo.

Necesita internet durante la instalación (unos 150 MB entre Python y las dependencias).

## Primer inicio

1. **Configuración → Iniciar sesión en Facebook**: se abre una ventana de Chrome (o Edge) con un
   perfil propio del monitor (carpeta `fb_profile/`, separada de tu Chrome normal). Inicia sesión
   a mano y **cierra esa ventana**. La sesión queda guardada para las siguientes revisiones.
   Conviene usar una cuenta secundaria: Facebook puede pedir verificación o limitar cuentas
   que navegan de forma automatizada.
2. **Configuración**: pega la API key de DeepSeek y la de Claude, elige con cuál se hacen las
   notas individuales y pulsa **Guardar**. Las keys quedan en `.env`, que no se sube a GitHub.
   La Fase 2 (cruce y boletín) usa Claude.
3. Pulsa **▶ Revisar ahora**.

## Uso

- **Páginas**: escribe la URL o el nombre (`radiopachamama`) y pulsa *Agregar*. *Activar/Pausar*
  la excluye de las revisiones sin borrarla (● activa, ○ pausada). Todo se guarda en `config.json`.
- **Intervalo**: por defecto 30 min; cámbialo y pulsa *Aplicar*.
- **Bandeja**: las nuevas aparecen resaltadas en amarillo y con notificación de Windows.
  Doble clic abre el PDF. Al seleccionar: texto, *Abrir en Facebook*, *Abrir nota externa*,
  *Abrir PDF*, *Reprocesar IA* (por ejemplo, tras cambiar de proveedor).
- **Barra de estado**: última revisión, próxima y último error. Log detallado en `data/monitor.log`.

### Primera revisión de una página

La primera vez que se revisa una página, solo se procesan con IA las
`procesar_en_primera_revision` publicaciones más recientes (3 por defecto). El resto se guarda
como "base" (ya vistas) para no gastar IA en noticias viejas.

## config.json

| Clave | Para qué |
|---|---|
| `paginas` | lista de `{url, activa}` |
| `intervalo_min` | minutos entre revisiones |
| `proveedor_ia` | `deepseek` o `claude` |
| `modelo_deepseek` / `modelo_claude` | modelo de cada proveedor |
| `posts_por_revision` | cuántos posts del feed leer por página |
| `procesar_en_primera_revision` | ver arriba |
| `navegador_canal` | `chrome`, `msedge` o `""` (Chromium de Playwright) |
| `headless` | `false` recomendado (Facebook detecta más el modo oculto) |

## Fase 2: cruce, clasificación, dashboard y boletín

Al final de cada revisión (si está activado en *Configuración*):

1. **Cruce y clasificación con Claude** (`monitor/analisis.py`). Las noticias que aún no tienen
   grupo se mandan en lotes de 12, junto con un resumen de una frase de los grupos de las
   últimas 48 h (no los textos completos). Claude devuelve para cada una: grupo (existente o
   nuevo), ámbito, tema y resumen.
2. **Nota combinada**: para los grupos con 2 o más medios se pide una nota única que junta los
   datos de todos, atribuye cada dato a su medio y marca las **discrepancias** en vez de elegir
   una versión. Los grupos de una sola fuente reutilizan la nota de la Fase 1, sin gastar API.
3. **Excel** `noticias.xlsx`, con las hojas *Noticias* y *Grupos*. Se regenera desde SQLite
   (la fuente principal). Si está abierto en Excel, se guarda una copia con fecha.
4. **Boletín PDF consolidado** en `pdfs/consolidados/AAAA-MM-DD_HHMM.pdf`: portada, índice por
   ámbito y tema con número de página (con enlace) y el desarrollo de cada noticia. Solo incluye
   lo que no salió antes. Si una noticia ya publicada recibe una fuente nueva, vuelve a salir
   marcada como **ACTUALIZACIÓN**.

Cada llamada a Claude (tokens de entrada y salida, duración, errores) queda en `data/api.log`.

**Dashboard**: tarjetas por ámbito con contadores (clic para filtrar), chips por tema, periodo
(hoy / 48 h / 7 días / todo), filtro de combinadas (◆ = 2 o más medios). Al seleccionar una
noticia se ven la nota combinada (con discrepancias resaltadas y enlaces clicables) y los
textos originales.

Los temas se editan en *Configuración → Temas* (o en `config.json`). Los nombres visibles de
los medios están en `nombres_medios`.

## Estructura

```
main.py               arranque
monitor/config.py     rutas, config.json, .env
monitor/storage.py    SQLite (data/posts.db): posts vistos y estado
monitor/scraper.py    Playwright: recorre el feed, extrae texto/fecha/permalink/enlace
monitor/ai.py         prompt + llamadas a DeepSeek / Claude, lectura de la nota externa
monitor/pdfgen.py     PDF con ReportLab -> pdfs/<pagina>/<fecha>_<titular>.pdf
monitor/worker.py     una revisión completa (corre en hilo aparte)
monitor/analisis.py   Fase 2: cruce, clasificación y nota combinada (Claude) + log de API
monitor/excel.py      exporta noticias.xlsx
monitor/consolidado.py boletín PDF consolidado (portada, índice con páginas, desarrollo)
monitor/gui.py        interfaz Tkinter: Bandeja · Dashboard · Configuración
```

## Cómo se detecta una publicación nueva

- ID principal: el `pfbid…` de `/posts/` (o el número de `/reel/`, `/videos/`, `photo?fbid=`).
  Para obtenerlo, el scraper pasa el mouse por la fecha del post, porque Facebook solo
  rellena el enlace real al hacer hover.
- Respaldo: hash del texto de la publicación. Un post se considera visto si coincide el ID
  **o** el hash.
- Se deja de bajar en el feed tras 3 publicaciones seguidas ya conocidas.

## Limitaciones conocidas

- Facebook cambia su HTML con frecuencia; si deja de detectar, revisar los selectores al
  inicio de `monitor/scraper.py` (`data-ad-rendering-role`, `aria-posinset`).
- Publicaciones solo con imagen o video sin texto quedan con estado `error` ("sin texto").
- Automatizar Facebook va contra sus términos de uso; úsese con moderación (intervalos
  largos, pocas páginas) y con una cuenta que no sea crítica.
