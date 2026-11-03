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

## Actualizar a la versión nueva (en cualquier PC)

Doble clic en **`ACTUALIZAR.bat`** (en la carpeta del programa). Cierra la app, baja la versión
nueva de GitHub (con Git si se instaló con `git clone`, o el ZIP si se instaló descargando el ZIP),
instala lo que falte y vuelve a abrir la app. **Conserva tus datos**: `.env` (keys), `data/`
(noticias, diseño, avisos, portada), `fb_profile/` (sesión de Facebook), `pdfs/`, `config.json` y
`noticias.xlsx`.

Si tu instalación es anterior a que existiera `ACTUALIZAR.bat`, bájalo una vez abriendo PowerShell
en la carpeta del programa y pegando:

```
(iwr https://raw.githubusercontent.com/Canazachyub/altiplano-al-dia/main/ACTUALIZAR.bat -UseBasicParsing).Content -replace "`r?`n","`r`n" | Set-Content ACTUALIZAR.bat -Encoding ascii -NoNewline; .\ACTUALIZAR.bat
```

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

La app abre en **🏠 Inicio**, que muestra el trabajo en 4 pasos con su estado y marca el
**👉 siguiente paso**:

1. **Buscar noticias**: *Revisar ahora* (o F5) y la revisión automática (pausar/activar).
2. **Elegir cuáles van**: lleva a **🗂 Noticias**, donde se marca ☑/☐ y se edita cada nota.
3. **Revisar el periódico**: abre la vista previa interactiva o **📰 Portada**.
4. **Publicar**: genera el PDF final.

Si falta algo para empezar (API keys, sesión de Facebook, páginas activas), Inicio lo muestra
arriba en amarillo con el botón para resolverlo. Al pasar el mouse sobre un botón aparece qué hace.

Abajo está el **📋 Registro de actividad**: lo que va haciendo el programa en vivo (qué página revisa,
noticias nuevas, IA, errores en rojo), con barra de desplazamiento para ver lo anterior. Se agranda
arrastrando su borde, se oculta con el botón de la barra de estado y *Mostrar detalle técnico* añade
las líneas internas del scraper.

- **Páginas**: escribe la URL o el nombre (`radiopachamama`) y pulsa *Agregar*. *Activar/Pausar*
  la excluye de las revisiones sin borrarla (● activa, ○ pausada). Todo se guarda en `config.json`.
- **Intervalo**: por defecto 30 min; cámbialo y pulsa *Aplicar*.
- **Controlar la revisión** (botones de la cabecera):
  - **▶ Revisar ahora**: revisa en ese momento.
  - **■ Detener**: aparece mientras se revisa (también en la franja amarilla). Corta la revisión en
    menos de un segundo y cierra el navegador; lo leído hasta ahí queda guardado.
  - **⏸ Pausar automático / ▶ Reanudar automático**: apaga o prende la revisión cada N minutos. En
    pausa solo se revisa al pulsar *Revisar ahora*. Se recuerda al cerrar la app.
- **Cerrar la app** detiene la revisión en curso, cierra el navegador del monitor y termina el
  programa. La app no se puede abrir dos veces: si ya está abierta, se muestra esa ventana.
  No cierres a mano la ventana de Chrome del monitor mientras revisa; usa **■ Detener**.
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
4. **Periódico PDF** en `pdfs/consolidados/AAAA-MM-DD_HHMM.pdf`, maquetado como diario impreso
   (`monitor/portada.py` y `monitor/consolidado.py`). Ya no se genera solo: primero lo revisas en
   el Dashboard (ver "Antes de maquetar") y lo generas con **📰 Generar periódico**.
   - **portada** de diario: cabezal con el logo, nota destacada arriba a la derecha, franja de
     fecha, banda "Hoy en Puno" con el conteo por ámbito, nota principal con titular gigante,
     foto y puntos clave, nota lateral y tres notas abajo, cada una con su etiqueta "‹‹ PÁG. N".
     Los titulares de portada los escribe Claude (una llamada por periódico) y quedan guardados
     en la nota para que puedas corregirlos;
   - página 2 con barra lateral: logo, fecha, sección en vertical sobre la ilustración del
     altiplano e índice **"En esta edición"** con número de página y enlace a cada nota;
   - páginas interiores con banda de cabecera (logo, sección con su icono, fecha y número de
     página) y grilla de 4 columnas: notas grandes en 3 columnas con antetítulo, titular (la
     primera línea en azul en la nota principal), firma "Redacción Altiplano al Día", foto con
     pie y texto justificado con guiones; notas cortas en la columna lateral;
   - caja **Datos clave** cuando sobra espacio;
   - al final, una **ficha de verificación** interna (medios, enlaces y discrepancias) que no es
     parte de lo publicable.

   Solo incluye lo que no salió antes. Si una noticia ya publicada recibe una fuente nueva,
   vuelve a salir marcada como **ACTUALIZACIÓN**. Las fotos se guardan al revisar cada página
   (`data/img/`); si el post no tiene foto se usa la imagen destacada de la nota enlazada.

   Recursos gráficos en `assets/`: el logo, los iconos de ámbitos y temas y la ilustración de
   portada (generados con ChatGPT) y las fuentes Zilla Slab, PT Serif y PT Sans Narrow
   (licencia OFL, incluidas en `assets/fonts/`).

Cada llamada a Claude (tokens de entrada y salida, duración, errores) queda en `data/api.log`.

### Antes de maquetar (Dashboard)

- **Qué noticias van**: la columna **Va** muestra ☑ (va) o ☐ (no va) en las noticias pendientes;
  un clic la cambia. *Incluir todas* / *Excluir todas* actúan sobre lo que se ve con los filtros.
  Las excluidas no se pierden: quedan pendientes por si luego las quieres incluir.
- **Editar una nota** (doble clic o *✎ Editar nota*): antetítulo, titular, lugar, pie de foto,
  bajada, cuerpo (párrafos separados por una línea en blanco; `## TEXTO` es un intertítulo),
  textos de portada y la **foto**: una de los posts de la noticia, una de tu PC
  (*Elegir archivo…*) o *Sin foto*. La plantilla no cambia, solo el contenido.
- **👁 Vista previa** abre el periódico **dentro de la app**, página por página (miniaturas a la
  izquierda, zoom, ◀ ▶ o la rueda del mouse), sin publicar nada:
  - al pasar el mouse sobre una noticia se marca y muestra su titular;
  - **clic** en una noticia: abre su editor (texto, foto, titular de portada, si va o no) y al
    guardar la vista previa se actualiza sola;
  - **clic derecho**: *Quitar del periódico* o *Poner en la portada como* Principal, Lateral,
    Destacada o Abajo 1-3;
  - **＋ Añadir noticia aquí**: los espacios libres de cada página aparecen marcados en verde; con un
    clic se elige una noticia (de las que no iban, de las ya publicadas o de todas) y se coloca en esa
    página (la página se reacomoda sola);
  - **📢 Avisos** en los espacios libres: «¿Tienes una noticia? Repórtala al WhatsApp…», «Anuncia con
    nosotros», «Envía tu foto» o la **imagen** de un anuncio. En la ventana *📢 Avisos* se crean y
    editan (título, mensaje, teléfono, icono o imagen) con vista previa; *Número para todos* pone tu
    número en todos. Los marcados *automático* rellenan solos los espacios libres; con clic en un
    espacio se elige *Poner una noticia* o *Poner un aviso*, y con clic en un aviso se cambia o se
    quita. Un aviso de texto sin teléfono no se imprime. Se guardan en `data/avisos.json`;
  - clic derecho → **Mover a la página…** fija en qué página va una noticia (y *Dejar que se ubique
    sola* lo deshace);
  - clic en una caja **Datos clave**: se editan los datos de las noticias que la forman (uno por
    línea; también desde el editor de cada nota);
  - **🎨 Diseño → Portada**: tipografía de cada parte de la portada (titular principal, otros
    titulares, antetítulos, puntos clave, cinta y banda, etiquetas) y su tamaño de letra;
  - **🎨 Diseño**: panel con los **textos fijos** (lema, cinta, banda, pies, firma, índice, caja de
    datos), los **colores** (principal, acento, alerta, texto y el de cada tema), las **tipografías**
    de cada parte y los **tamaños**. Trae combinaciones listas (*Altiplano*, *Clásico sobrio*,
    *Moderno*, *Alto contraste*) y *Volver al diseño original*. Se guarda en `data/diseno.json` y se
    usa también al publicar;
  - **📰 Publicar este periódico** lo publica tal como se ve.
- **📰 Generar periódico** lo genera de verdad y marca esas noticias como publicadas.
- **Pestaña Portada**: 6 lugares (principal, lateral, destacada y tres abajo). En cada uno eliges la
  noticia, su foto (*Cambiar foto…*, *Otra foto de la noticia*, *Sin foto*) y los textos de portada.
  Avisa si la foto es pequeña para ese lugar (se vería borrosa). *Titulares con Claude* escribe los
  que dejes vacíos; *Elegir automáticamente* vuelve a la selección por importancia y foto.
- Mientras corre una revisión o un análisis aparece una franja amarilla con el progreso; cada
  acción (incluir, excluir, guardar) muestra un aviso abajo a la derecha. Una noticia ya publicada
  se puede volver a incluir (con *Mostrar: Todas* y clic en su columna *¿Va?*).

Tipografías de la portada: Alfa Slab One (titulares) y Fira Sans Condensed (antetítulos, puntos y
etiquetas); cada tema tiene su color.

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
