# Inventario de la interfaz de mANY-MAZE (generado)

**1435 controles en 101 contextos.**

Generado por `qa/herramientas/generar_inventario.py` con el experimento de demostración abierto. **No editar a mano**: vuelve a generarlo cuando cambie la interfaz.

Cada fila es un control que la prueba debe tocar al menos una vez. El ID es estable (contexto + nombre). En la bitácora, anota cada ID con su resultado: `OK`, `FALLA` (con el ID del hallazgo), `BLOQUEADO` (motivo) o `N/A` (motivo, p. ej. sin hardware).

Leyenda de tipos: botón, botón de cinta, menú, casilla, opción, lista (desplegable), número, texto, deslizador, tabla/lista, diálogo.

## Barra de menús de macOS

Cómo llegar: barra superior de la pantalla

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `BARDEMENDEMA-9437` | File | menú | New experiment… |  | Ctrl+N |
| `BARDEMENDEMA-3283` | File | menú | Open experiment… |  | Ctrl+O |
| `BARDEMENDEMA-F6CF` | File | menú | Open recent | submenú:  |  |
| `BARDEMENDEMA-41DD` | File | menú | Create demo experiment |  |  |
| `BARDEMENDEMA-D323` | File | menú | Save |  | Ctrl+S |
| `BARDEMENDEMA-49E3` | File | menú | Save as… |  | Ctrl+Shift+S |
| `BARDEMENDEMA-1085` | File | menú | Close experiment |  |  |
| `BARDEMENDEMA-650C` | File | menú | Current user… |  |  |
| `BARDEMENDEMA-6725` | File | menú | Users and security… |  |  |
| `BARDEMENDEMA-36F1` | File | menú | Reveal experiment folder |  |  |
| `BARDEMENDEMA-86B4` | File | menú | Protocol report… |  |  |
| `BARDEMENDEMA-E1A1` | File | menú | Restore a backup… |  |  |
| `BARDEMENDEMA-26CE` | File | menú | Archive experiment… |  |  |
| `BARDEMENDEMA-DDA8` | File | menú | Open archive… |  |  |
| `BARDEMENDEMA-7843` | File | menú | Protect experiment… |  |  |
| `BARDEMENDEMA-D258` | File | menú | Allowed programs… |  |  |
| `BARDEMENDEMA-6237` | mANY-MAZE (menú de la aplicación; en el código, File) | menú | Quit |  |  |
| `BARDEMENDEMA-1C55` | View | menú | Appearance | submenú: System ¦ Light ¦ Dark |  |
| `BARDEMENDEMA-DF9D` | Go | menú | Protocol |  | Ctrl+1 |
| `BARDEMENDEMA-DC25` | Go | menú | Animals |  | Ctrl+2 |
| `BARDEMENDEMA-4DAF` | Go | menú | Apparatus |  | Ctrl+3 |
| `BARDEMENDEMA-1C3D` | Go | menú | Test schedule |  | Ctrl+4 |
| `BARDEMENDEMA-7AC5` | Go | menú | Test view |  | Ctrl+5 |
| `BARDEMENDEMA-15F1` | Go | menú | Run tests |  | Ctrl+6 |
| `BARDEMENDEMA-308F` | Go | menú | Results |  | Ctrl+7 |
| `BARDEMENDEMA-B3B2` | Go | menú | Statistics |  | Ctrl+8 |
| `BARDEMENDEMA-54F1` | Help | menú | User guide |  |  |
| `BARDEMENDEMA-4D09` | Help | menú | Check for updates… |  |  |
| `BARDEMENDEMA-041F` | Help | menú | Check for updates at startup |  |  |
| `BARDEMENDEMA-61E5` | mANY-MAZE (menú de la aplicación; en el código, Help) | menú | About mANY-MAZE |  |  |

## Pestaña File (sin experimento abierto)

Cómo llegar: pestaña azul File

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PESFILSINEXP-7BF6` |  | botón | New experiment |  |  |
| `PESFILSINEXP-2A73` |  | botón | Open experiment |  |  |
| `PESFILSINEXP-79C7` |  | botón | Create demo experiment |  |  |
| `PESFILSINEXP-DB07` |  | botón | Save |  | deshabilitado al inicio |
| `PESFILSINEXP-FA9E` |  | botón | Save as |  | deshabilitado al inicio |
| `PESFILSINEXP-138D` |  | botón | Close experiment |  | deshabilitado al inicio |
| `PESFILSINEXP-A8D8` |  | botón | Import from ANY-maze |  | deshabilitado al inicio |
| `PESFILSINEXP-57D7` |  | botón | Protocol report |  | deshabilitado al inicio |
| `PESFILSINEXP-5035` |  | botón | Restore a backup |  | deshabilitado al inicio |
| `PESFILSINEXP-8976` |  | botón | Archive experiment |  | deshabilitado al inicio |
| `PESFILSINEXP-E896` |  | botón | Open archive |  |  |
| `PESFILSINEXP-F4A6` |  | botón | Protect experiment |  | deshabilitado al inicio |
| `PESFILSINEXP-5912` |  | botón | Show in folder |  | deshabilitado al inicio |
| `PESFILSINEXP-2100` |  | botón | Allowed programs |  |  |
| `PESFILSINEXP-7D13` |  | botón | User guide |  |  |
| `PESFILSINEXP-373C` |  | botón | About |  |  |
| `PESFILSINEXP-591D` |  | tabla/lista | Recent experiments | columnas: 1 |  |

## Esquina superior derecha de la cinta

Cómo llegar: siempre visible

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `ESQSUPDERDEL-9678` | esquina de la cinta | botón | No user | menú: No user ¦ New user… ¦ Set password… ¦ Users and security… ¦ Remove a user from this experiment… |  |
| `ESQSUPDERDEL-E10B` | esquina de la cinta | botón | Save the experiment (Ctrl+S) |  |  |

## Protocol ▸ Protocol — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROCIN-9C91` | Protocol | botón de cinta | Protocol |  |  |
| `PROPROCIN-2120` | Protocol | botón de cinta | Apparatus |  |  |
| `PROPROCIN-E0E4` | Protocol | botón de cinta | Add item | menú: New apparatus ¦ New stage ¦ New key ¦ New procedure ¦ New time period ¦ New event-based time period ¦ New training criterion ¦ New calculation |  |
| `PROPROCIN-055E` | Testing | botón de cinta | Blind testing |  |  |
| `PROPROCIN-B8FB` | Templates | botón de cinta | Apply template | menú: Open field ¦ Elevated plus maze ¦ Elevated zero maze ¦ Y maze (spontaneous alternation) ¦ T maze ¦ Radial arm maze ¦ Radial arm place conditioning (RAPC) ¦ Morris water maze ¦ Barnes maze ¦ Novel object recognition ¦ Light/dark box ¦ Three-chamber sociability ¦ Fear conditioning / freezing ¦ Forced swim test (Porsolt) ¦ Tail suspension test ¦ Novel tank diving test (fish) ¦ Multi-well plate (larvae) ¦ Conditioned place preference ¦ Hole board ¦ Thermal gradient ring ¦ Home cage ¦ Activity wheel / circular runway ¦ Custom |  |
| `PROPROCIN-CC36` | Templates | botón de cinta | Apparatus from template |  |  |
| `PROPROCIN-8098` |  | botón de cinta | No user | menú: No user ¦ New user… ¦ Set password… ¦ Users and security… ¦ Remove a user from this experiment… |  |

## Protocol ▸ Protocol

Cómo llegar: pestaña Protocol → explorador «Protocol»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPRO-8F50` |  | texto | Protocol name |  |  |
| `PROPRO-EFCB` |  | texto largo | Description |  |  |
| `PROPRO-9A21` |  | lista | Type of test | valores: Open field ¦ Elevated plus maze ¦ Elevated zero maze ¦ Y maze (spontaneous alternation) ¦ T maze ¦ Radial arm maze ¦ Radial arm place conditioning (RAPC) ¦ Morris water maze ¦ Barnes maze ¦ Novel object recognition ¦ Light/dark box ¦ Three-chamber sociability ¦ Fear conditioning / freezing ¦ Forced swim test (Porsolt) ¦ Tail suspension test ¦ Novel tank diving test (fish) ¦ Multi-well plate (larvae) ¦ Conditioned place preference ¦ Hole board ¦ Thermal gradient ring ¦ Home cage ¦ Activity wheel / circular runway ¦ Custom |  |
| `PROPRO-1302` |  | lista | Mode | valores: Video tracking — the animal is tracked and keys can be scored ¦ TakeNote — behaviours are scored by hand only (no tracking) ¦ Input/output only — tests run with the I/O devices and procedures (no video) |  |
| `PROPRO-4CF2` |  | número | Each test lasts |  |  |
| `PROPRO-6E5F` |  | lista | Tests start | valores: At the test start time set for each test ¦ When the animal is first detected in the apparatus ¦ When the experimenter's hand has left the image |  |
| `PROPRO-7996` |  | casilla | Blind testing — hide the treatments (shown as codes) while testing and scoring |  |  |
| `PROPRO-CB14` |  | casilla | Confirm the animal's ID before each test |  |  |
| `PROPRO-EA6B` |  | casilla | Weigh the animal before each live test (when a balance is connected) |  |  |
| `PROPRO-FEB7` |  | tabla/lista | (sin etiqueta) | columnas: Term ¦ Called ¦ Plural |  |

## Protocol ▸ Protocol ▸ Animal tracking — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Animal tracking»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROANITRA-04A0` | Animal tracking | botón de cinta | Install pose model |  |  |
| `PROPROANITRA-563A` | Animal tracking | botón de cinta | Restore defaults |  |  |

## Protocol ▸ Protocol ▸ Animal tracking

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Animal tracking»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROANITRA-3B12` |  | lista | Detect the animal using | valores: Background subtraction ¦ A grey-level threshold ¦ Its colour (or a colour mark) |  |
| `PROPROANITRA-8938` |  | lista | Compared with the background the animal is | valores: Darker or lighter ¦ Darker ¦ Lighter |  |
| `PROPROANITRA-86B7` |  | número | Detection threshold (0 = automatic) |  |  |
| `PROPROANITRA-3EA1` |  | lista | Build the background from | valores: The median of sampled frames ¦ An empty-arena frame ¦ An adaptive model |  |
| `PROPROANITRA-2CFF` |  | número | Empty-arena frame number |  |  |
| `PROPROANITRA-DC95` |  | número | Frames sampled for the median |  |  |
| `PROPROANITRA-B584` |  | casilla | Ignore lighting changes |  |  |
| `PROPROANITRA-5F7D` |  | número | Ignore objects smaller than (px²) |  |  |
| `PROPROANITRA-C49C` |  | número | Ignore objects larger than (px², 0 = no limit) |  |  |
| `PROPROANITRA-433E` |  | texto | Colour of the animal or mark (#rrggbb) |  |  |
| `PROPROANITRA-260C` |  | número | Colour tolerance (hue, degrees) |  |  |
| `PROPROANITRA-4F65` |  | número | Ignore pixels greyer than (saturation 0–255) |  |  |
| `PROPROANITRA-5183` |  | texto | Identify several animals by colour marks |  |  |
| `PROPROANITRA-0372` |  | casilla | Detect the head and the tail |  |  |
| `PROPROANITRA-633A` |  | número | Tail removal strength |  |  |
| `PROPROANITRA-1E73` |  | casilla | Record the animal's whole-body outline |  |  |
| `PROPROANITRA-8A7E` |  | lista | Locate the body parts using | valores: The animal's shape ¦ A pose model (AI) |  |
| `PROPROANITRA-20BB` |  | número | Minimum keypoint confidence |  |  |
| `PROPROANITRA-5C83` |  | lista | Run the pose model on | valores: The fastest device available ¦ The CPU |  |
| `PROPROANITRA-C526` |  | número | Blur the image by (px) |  |  |
| `PROPROANITRA-A502` |  | número | Remove specks up to (px) |  |  |
| `PROPROANITRA-14A2` |  | número | Fill holes up to (px) |  |  |
| `PROPROANITRA-3B54` |  | número | Erase thin wires and bars up to (px) |  |  |
| `PROPROANITRA-0559` |  | número | Remove jumps faster than (units/s, 0 = off) |  |  |
| `PROPROANITRA-A8CF` |  | número | … when the track comes back within (s) |  |  |
| `PROPROANITRA-16ED` |  | casilla | Otherwise remove jumps faster than 300 cm/s (calibrated apparatus) |  |  |
| `PROPROANITRA-CB8B` |  | número | A pixel is moving when it changes by (grey levels) |  |  |
| `PROPROANITRA-FCE7` |  | lista | Count the pixel change of one animal in | valores: The whole arena ¦ The area around the animal |  |
| `PROPROANITRA-F313` |  | número | Fill gaps in the track of up to (s) |  |  |
| `PROPROANITRA-05B8` |  | número | Smooth positions over (frames, 0 = off) |  |  |
| `PROPROANITRA-FD99` |  | número | Analyse every Nth frame |  |  |
| `PROPROANITRA-F991` |  | lista | Downscale the video before tracking | valores: No (full resolution) ¦ By 2 (half the width and height) ¦ By 4 (a quarter) |  |
| `PROPROANITRA-5971` |  | lista | Set the detection up for | valores: Choose an animal… ¦ Mouse ¦ Rat ¦ Hooded rat ¦ White animal on sawdust ¦ Zebrafish |  |
| `PROPROANITRA-B4B7` | Pose model (AI body parts) | lista | The animal filmed: the built-in model is for mice; for rats and other animals us | valores: Mouse ¦ Rat ¦ Another animal |  |
| `PROPROANITRA-FA11` | Pose model (AI body parts) | botón | Install… |  |  |
| `PROPROANITRA-2CC2` | Pose model (AI body parts) | botón | Custom ONNX… |  |  |
| `PROPROANITRA-71BB` | Pose model (AI body parts) | botón | Convert DeepLabCut model… |  |  |

## Protocol ▸ Protocol ▸ Stages — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Stages»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROSTACIN-D20A` | Stages | botón de cinta | New stage |  |  |
| `PROPROSTACIN-684D` | Stages | botón de cinta | New criterion |  |  |
| `PROPROSTACIN-11C9` | Stages | botón de cinta | Delete criterion |  |  |

## Protocol ▸ Protocol ▸ Stages

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Stages»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROSTA-889F` |  | texto largo | Enter the stages, one per line |  |  |
| `PROPROSTA-B482` |  | tabla/lista | (sin etiqueta) | columnas: Stage ¦ Measure ¦ Is ¦ Value ¦ Consecutive trials ¦ When met ¦ Retire after ¦ Minimum trials ¦ Variability ¦ Variability of ¦ Variability over ¦ Variability at most |  |
| `PROPROSTA-5932` |  | lista | Variability (%): ANY-maze's ((highest − lowest) / (highest + lowest)) × 100 over | valores: No variability rule ¦ Variability (%) ¦ SD ¦ CV (%) | deshabilitado al inicio |
| `PROPROSTA-70A5` |  | texto | the criterion's measure |  | deshabilitado al inicio |
| `PROPROSTA-DE90` |  | número | (sin etiqueta) |  | deshabilitado al inicio |
| `PROPROSTA-DE902` |  | número | (sin etiqueta) |  | deshabilitado al inicio |
| `PROPROSTA-E2B2` |  | botón | Add criterion |  |  |
| `PROPROSTA-589B` |  | botón | Remove |  |  |

## Protocol ▸ Protocol ▸ Keys — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Keys»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROKEYCIN-4A4A` | Keys | botón de cinta | New key |  |  |
| `PROPROKEYCIN-05B8` | Keys | botón de cinta | Delete key |  |  |
| `PROPROKEYCIN-345D` | Keys | botón de cinta | Duplicate key |  |  |

## Protocol ▸ Protocol ▸ Keys

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Keys»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROKEY-AD41` |  | tabla/lista | Key stroke: a letter, digit or punctuation key (up to 46 keys). Keys in the same | columnas: Key name ¦ Key stroke ¦ How it works ¦ Radio set ¦ Colour |  |
| `PROPROKEY-9E25` |  | lista | Simple: while the key is held down · Toggle: first press starts, second press en | valores: Simple ¦ Toggle ¦ Radio ¦ Event |  |
| `PROPROKEY-1886` |  | botón | Colour of the on-screen scoring button |  |  |
| `PROPROKEY-9E252` |  | lista | Simple: while the key is held down · Toggle: first press starts, second press en | valores: Simple ¦ Toggle ¦ Radio ¦ Event |  |
| `PROPROKEY-18862` |  | botón | Colour of the on-screen scoring button |  |  |
| `PROPROKEY-9E253` |  | lista | Simple: while the key is held down · Toggle: first press starts, second press en | valores: Simple ¦ Toggle ¦ Radio ¦ Event |  |
| `PROPROKEY-18863` |  | botón | Colour of the on-screen scoring button |  |  |
| `PROPROKEY-1016` |  | botón | New key |  |  |
| `PROPROKEY-0730` |  | botón | Delete key |  |  |
| `PROPROKEY-8239` |  | texto | Key name |  |  |
| `PROPROKEY-721E` |  | lista | Key stroke | valores: A ¦ B ¦ C ¦ D ¦ E ¦ F ¦ G ¦ H ¦ I ¦ J ¦ K ¦ L ¦ M ¦ N ¦ O ¦ P ¦ Q ¦ R ¦ S ¦ T ¦ U ¦ V ¦ W ¦ X ¦ Y ¦ …(+21) |  |
| `PROPROKEY-3E54` |  | lista | Specify how you'd like this key to work | valores: Simple - key activity is occurring while key is pressed ¦ Toggle - key activity starts on first press and ends on second press ¦ Radio - like toggle but also ends if any other radio key is pressed ¦ Event - an instantaneous event, scored when the key is pressed |  |
| `PROPROKEY-D995` |  | botón | Colour of the scoring button |  |  |
| `PROPROKEY-F3D8` |  | casilla | This behaviour counts as activity |  |  |

## Protocol ▸ Protocol ▸ Procedures — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Procedures»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROPROCIN-0519` | Procedures | botón de cinta | New procedure |  |  |
| `PROPROPROCIN-32E6` | Procedures | botón de cinta | Duplicate procedure |  |  |
| `PROPROPROCIN-3BA6` | Procedures | botón de cinta | Delete procedure |  |  |
| `PROPROPROCIN-7C21` | Procedures | botón de cinta | Edit as text |  |  |
| `PROPROPROCIN-64A6` | Procedures | botón de cinta | Functions |  |  |

## Protocol ▸ Protocol ▸ Procedures

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Procedures»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROPRO-A7DD` |  | botón | Functions… |  |  |
| `PROPROPRO-E95E` |  | botón | Add ▾ | menú: When ¦ Wait ¦ If ¦ Repeat ¦ Set ¦ Do ¦ Stop ¦ Comment ¦ Variable ¦ Call sub-procedure ¦ Label ¦ Go to ¦ Set timer resolution |  |
| `PROPROPRO-CCC9` |  | botón | Add inside ▾ | menú: When ¦ Wait ¦ If ¦ Repeat ¦ Set ¦ Do ¦ Stop ¦ Comment ¦ Variable ¦ Call sub-procedure ¦ Label ¦ Go to ¦ Set timer resolution | deshabilitado al inicio |
| `PROPROPRO-76AA` |  | botón | Remove |  | deshabilitado al inicio |
| `PROPROPRO-1D34` |  | botón | Copy |  | deshabilitado al inicio |
| `PROPROPRO-BB45` |  | botón | ▲ |  | deshabilitado al inicio |
| `PROPROPRO-A08E` |  | botón | ▼ |  | deshabilitado al inicio |
| `PROPROPRO-D329` |  | botón | → |  | deshabilitado al inicio |
| `PROPROPRO-A8D4` |  | botón | ← |  | deshabilitado al inicio |
| `PROPROPRO-72FC` |  | botón | On/off |  | deshabilitado al inicio |
| `PROPROPRO-6542` |  | botón | JSON |  | deshabilitado al inicio |
| `PROPROPRO-AFD0` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROPROPRO-97E3` |  | tabla/lista | Problems found while checking the procedures; click one to select it | columnas: 1 |  |
| `PROPROPRO-8131` |  | tabla/lista | <b>Procedures</b> | columnas: 1 |  |
| `PROPROPRO-30B0` |  | botón | Add | menú: Empty procedure ¦ From an example |  |
| `PROPROPRO-9180` |  | botón | Duplicate |  | deshabilitado al inicio |
| `PROPROPRO-76AA2` |  | botón | Remove |  | deshabilitado al inicio |
| `PROPROPRO-BB452` |  | botón | ▲ |  | deshabilitado al inicio |
| `PROPROPRO-A08E2` |  | botón | ▼ |  | deshabilitado al inicio |
| `PROPROPRO-F7F4` |  | casilla | Sub-procedure |  | deshabilitado al inicio |
| `PROPROPRO-95DC` |  | casilla | ANY-maze maths |  | deshabilitado al inicio |

## Protocol ▸ Protocol ▸ Analysis — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Analysis»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROANACIN-305A` | Analysis | botón de cinta | New time period |  |  |
| `PROPROANACIN-47AA` | Analysis | botón de cinta | New event period |  |  |
| `PROPROANACIN-77AF` | Analysis | botón de cinta | Delete time period |  |  |
| `PROPROANACIN-3402` | Analysis | botón de cinta | Restore defaults |  |  |

## Protocol ▸ Protocol ▸ Analysis

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Analysis»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROANA-2466` |  | número | Smooth positions for distance and speed over (s) |  |  |
| `PROPROANA-9DFB` |  | número | The animal is immobile below (units/s) |  |  |
| `PROPROANA-C87D` |  | número | Shortest immobility episode (s) |  |  |
| `PROPROANA-EFDC` |  | número | Partial rotation angle (°) |  |  |
| `PROPROANA-EE31` |  | lista | Detect immobility | valores: From the speed of the animal's centre ¦ Forced swim / tail suspension: from the struggle in the image |  |
| `PROPROANA-1341` |  | número | The animal struggles when its struggle index reaches (% of body) |  |  |
| `PROPROANA-1E85` |  | número | Shortest immobile period (s) |  |  |
| `PROPROANA-7404` |  | casilla | Forced swim: split the struggle into climbing and swimming |  |  |
| `PROPROANA-91CD` |  | número | Climbing when the struggle index reaches (% of body) |  |  |
| `PROPROANA-B4E8` |  | lista | Freezing thresholds | valores: Set manually (below) ¦ Automatic, from the motion of each test |  |
| `PROPROANA-B964` |  | número | Automatic threshold sensitivity (0–100) |  |  |
| `PROPROANA-2CA5` |  | número | Freezing starts when movement falls below (% of body) |  |  |
| `PROPROANA-2079` |  | número | Freezing ends when movement rises above (% of body) |  |  |
| `PROPROANA-2011` |  | número | Shortest freezing episode (s) |  |  |
| `PROPROANA-E2F9` |  | lista | The animal is active when | valores: It is mobile, or doing a behaviour that counts as activity (ANY-maze) ¦ It is doing a behaviour that counts as activity (ANY-maze without mobility) ¦ Its pixels change (movement threshold below) |  |
| `PROPROANA-0990` |  | número | The animal is active when movement reaches (% of body) |  |  |
| `PROPROANA-6EEF` |  | número | Shortest inactive episode (s) |  |  |
| `PROPROANA-D67C` |  | casilla | Detect rearing automatically |  |  |
| `PROPROANA-F6F4` |  | número | A rear makes the body area fall below (% of usual) |  |  |
| `PROPROANA-E606` |  | número | … and the head–tail length below (% of usual) |  |  |
| `PROPROANA-E3BF` |  | número | Shortest rear (s) |  |  |
| `PROPROANA-B604` |  | lista | Decide whether the animal is in a zone using its | valores: Centre of body ¦ Head ¦ Tail base ¦ Proportion of the body |  |
| `PROPROANA-2730` |  | número | Part of the body that must be in the zone (%) |  |  |
| `PROPROANA-9423` |  | número | A lost animal is in a hidden zone within (units) |  |  |
| `PROPROANA-AA2D` |  | número | Ignore zone visits shorter than (s) |  |  |
| `PROPROANA-FAE2` |  | casilla | Count starting the test in a zone as an entry |  |  |
| `PROPROANA-4F0B` |  | lista | When an event never occurs, its latency is | valores: The test duration ¦ Left blank |  |
| `PROPROANA-1EA4` |  | lista | An average of nothing is | valores: Left blank (ANY-maze) ¦ Zero (ANY-maze's “Use zero for undefined averages”) ¦ As before (0 for visits, investigation bouts and rears in a zone, else blank) |  |
| `PROPROANA-9A6D` |  | lista | Initial heading: the first position to the position | valores: After the animal has been mobile for (s) ¦ First further away than (units) |  |
| `PROPROANA-4A16` |  | número | … mobile for (s) |  |  |
| `PROPROANA-CCF1` |  | número | … further away than (units) |  |  |
| `PROPROANA-4849` |  | número | Thigmotaxis band next to the wall (units) |  |  |
| `PROPROANA-CE0B` |  | número | Exploring means facing the object within (°) |  |  |
| `PROPROANA-0660` |  | número | Oriented towards a zone / point within (°) |  |  |
| `PROPROANA-1C00` |  | número | Grid for line crossings (N×N, 0 = off) |  |  |
| `PROPROANA-B51A` |  | número | Social contact within (units) |  |  |
| `PROPROANA-1BA9` |  | número | Nose contact within (units) |  |  |
| `PROPROANA-9894` |  | número | Following within (units) |  |  |
| `PROPROANA-B791` |  | casilla | Report the time in each quadrant of the arena |  |  |
| `PROPROANA-04A4` |  | casilla | Split the scored behaviours by zone |  |  |
| `PROPROANA-F761` |  | número | Whishaw corridor width (units) |  |  |
| `PROPROANA-BE70` |  | texto | Drug-paired chamber (place preference) |  |  |
| `PROPROANA-1D6E` |  | texto | The novel object is the point named |  |  |
| `PROPROANA-F920` |  | lista | The social stimulus is on the | valores: Left ¦ Right |  |
| `PROPROANA-514C` |  | lista | Barnes maze search strategy | valores: Simple: from the holes visited before the escape hole ¦ ANY-maze (Gawel et al. 2018): overall and primary strategy ¦ UNMC method: the strategies used in turn |  |
| `PROPROANA-5D10` |  | número | Target region: holes either side of the escape hole |  |  |
| `PROPROANA-B919` |  | número | A serial strategy starts after (consecutive hole visits) |  |  |
| `PROPROANA-8674` |  | número | Holes the animal may skip in a serial strategy |  |  |
| `PROPROANA-CB9B` |  | texto | Centre zone of the Barnes maze |  |  |
| `PROPROANA-305C` |  | número | Analogue inputs: baseline period (s) |  |  |
| `PROPROANA-A050` |  | número | Analogue inputs: a deviation is more than (baseline SD) |  |  |
| `PROPROANA-A8ED` |  | texto | OPAD: paw contact input |  |  |
| `PROPROANA-34C3` |  | texto | OPAD: lick input |  |  |
| `PROPROANA-0F3D` |  | texto | OPAD: temperature input |  |  |
| `PROPROANA-D98E` |  | texto | OPAD: temperatures of interest |  |  |
| `PROPROANA-0172` |  | número | OPAD: at a temperature within ± |  |  |
| `PROPROANA-03A9` |  | texto | End the test when the animal stays in zone |  |  |
| `PROPROANA-975D` |  | número | … for at least (s) |  |  |
| `PROPROANA-2F75` |  | número | Split each test into time bins of (s, 0 = off) |  |  |
| `PROPROANA-7C07` |  | tabla/lista | (sin etiqueta) | columnas: Time period ¦ Starts at (s) ¦ Ends at (s) |  |
| `PROPROANA-21F2` |  | botón | New time period |  |  |
| `PROPROANA-302E` |  | botón | Remove |  |  |
| `PROPROANA-7C072` |  | tabla/lista | (sin etiqueta) | columnas: Time period ¦ The period starts at ¦ Zone / key / input / calculation ¦ Offset (s) ¦ Duration (s) ¦ Occurrence ¦ The period ends ¦ Ends at zone / key / input / calculation ¦ End offset (s) ¦ End occurrence |  |
| `PROPROANA-3737` |  | botón | New event period |  |  |
| `PROPROANA-302E2` |  | botón | Remove |  |  |
| `PROPROANA-7C073` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROPROANA-19EA` |  | botón | Add plug-in | menú: Data file (CSV / TSV) |  |
| `PROPROANA-6452` |  | botón | Edit… |  |  |
| `PROPROANA-302E3` |  | botón | Remove |  |  |
| `PROPROANA-90DF` |  | botón | Run on the tests |  |  |

## Protocol ▸ Protocol ▸ Calculations — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Calculations»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROCALCIN-45D3` | Calculations | botón de cinta | New calculation |  |  |
| `PROPROCALCIN-A023` | Calculations | botón de cinta | Delete calculation |  |  |
| `PROPROCALCIN-192C` | Calculations | botón de cinta | Duplicate calculation |  |  |

## Protocol ▸ Protocol ▸ Calculations

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Calculations»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROCAL-5621` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROPROCAL-F81A` |  | botón | New calculation |  |  |
| `PROPROCAL-39C2` |  | botón | Delete |  |  |
| `PROPROCAL-DF70` |  | texto | Calculation name |  | deshabilitado al inicio |
| `PROPROCAL-FBE6` |  | número | Decimal places of the result |  | deshabilitado al inicio |
| `PROPROCAL-B981` |  | texto | Units of the result |  | deshabilitado al inicio |
| `PROPROCAL-270C` |  | texto | Maximum of the Y axis |  | deshabilitado al inicio |
| `PROPROCAL-6675` |  | texto | Minimum of the Y axis |  | deshabilitado al inicio |
| `PROPROCAL-F5BF` |  | texto | Name |  | deshabilitado al inicio |
| `PROPROCAL-7B76` |  | texto | Value |  | deshabilitado al inicio |
| `PROPROCAL-F5BF2` |  | texto | Name |  | deshabilitado al inicio |
| `PROPROCAL-7B762` |  | texto | Value |  | deshabilitado al inicio |
| `PROPROCAL-2416` |  | texto largo | 100 * {Open arms: time (s)} / {Test duration (s)} |  | deshabilitado al inicio |
| `PROPROCAL-2A30` |  | botón | Insert measure… |  | deshabilitado al inicio |
| `PROPROCAL-09BD` |  | botón | Insert function | menú: Result for part of the test ¦ Result for a trial ¦ Result for the last trial of a stage ¦ Count across the trials of a stage ¦ Sum across the trials of a stage ¦ Mean across the trials of a stage ¦ Max across the trials of a stage ¦ Min across the trials of a stage ¦ abs() ¦ sqrt() ¦ log10() ¦ log() ¦ round() ¦ min() ¦ max() ¦ is_undefined() | deshabilitado al inicio |

## Protocol ▸ Protocol ▸ Hardware — CINTA

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Hardware»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROHARCIN-0EB8` | Hardware | botón de cinta | I/O devices |  |  |
| `PROPROHARCIN-7891` | Hardware | botón de cinta | Touch screen |  |  |

## Protocol ▸ Protocol ▸ Hardware

Cómo llegar: pestaña Protocol → explorador «Protocol» → «Hardware»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROPROHAR-2CCB` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROPROHAR-9289` |  | botón | Set up I/O devices… |  |  |
| `PROPROHAR-3D9E` |  | casilla | Send synchronisation pulses in live tests |  |  |
| `PROPROHAR-1908` |  | lista | Output |  | deshabilitado al inicio |
| `PROPROHAR-7F57` |  | casilla | When the test starts |  | deshabilitado al inicio |
| `PROPROHAR-339B` |  | casilla | When the test ends |  | deshabilitado al inicio |
| `PROPROHAR-E4B9` |  | casilla | For every captured frame (while the test runs or is paused) |  | deshabilitado al inicio |
| `PROPROHAR-78A4` |  | casilla | For every position stored in the track |  | deshabilitado al inicio |
| `PROPROHAR-E11D` |  | número | Pulse width |  | deshabilitado al inicio |
| `PROPROHAR-22B9` |  | botón | Touch screen settings… |  |  |

## Protocol ▸ Apparatus — CINTA

Cómo llegar: pestaña Protocol → explorador «Apparatus»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPPCIN-C915` | Apparatus | botón de cinta | From template |  |  |
| `PROAPPCIN-671B` | Apparatus | botón de cinta | New |  |  |
| `PROAPPCIN-259B` | Apparatus | botón de cinta | Duplicate |  |  |
| `PROAPPCIN-AA4A` | Apparatus | botón de cinta | Rename |  |  |
| `PROAPPCIN-9DF5` | Apparatus | botón de cinta | Delete |  |  |
| `PROAPPCIN-0A1E` | Apparatus | botón de cinta | Import |  |  |
| `PROAPPCIN-D431` | Apparatus | botón de cinta | Export |  |  |
| `PROAPPCIN-8EC6` | Apparatus map | botón de cinta | Select objects |  |  |
| `PROAPPCIN-54FC` | Apparatus map | botón de cinta | Multiline tool |  |  |
| `PROAPPCIN-1935` | Apparatus map | botón de cinta | Rectangle tool |  |  |
| `PROAPPCIN-BFD0` | Apparatus map | botón de cinta | Ellipse tool |  |  |
| `PROAPPCIN-39BD` | Apparatus map | botón de cinta | Line tool |  |  |
| `PROAPPCIN-D103` | Apparatus map | botón de cinta | Select all |  |  |
| `PROAPPCIN-424B` | Apparatus map | botón de cinta | Delete selection |  |  |
| `PROAPPCIN-9AE1` | Apparatus map | botón de cinta | Points attract |  |  |
| `PROAPPCIN-0CD2` | Define | botón de cinta | Arena | menú: Rectangle ¦ Ellipse / circle ¦ Polygon |  |
| `PROAPPCIN-4620` | Define | botón de cinta | Point |  |  |
| `PROAPPCIN-C2AA` | Define | botón de cinta | Zone grid |  |  |
| `PROAPPCIN-EAEC` | Define | botón de cinta | Zone group |  |  |
| `PROAPPCIN-CE75` | Define | botón de cinta | Sequence |  |  |
| `PROAPPCIN-1EBB` | Calibration | botón de cinta | Ruler |  |  |
| `PROAPPCIN-259A` | Calibration | botón de cinta | Clear |  |  |
| `PROAPPCIN-FC44` | Background | botón de cinta | Video file |  |  |
| `PROAPPCIN-3680` | Background | botón de cinta | Test video | menú: Test 1: C1 · C1.avi (this apparatus) ¦ Test 2: C2 · C2.avi (this apparatus) ¦ Test 3: C3 · C3.avi (this apparatus) ¦ Test 4: A1 · A1.avi (this apparatus) ¦ Test 5: A2 · A2.avi (this apparatus) ¦ Test 6: A3 · A3.avi (this apparatus) |  |
| `PROAPPCIN-0C64` | Background | botón de cinta | Lens correction |  |  |
| `PROAPPCIN-FE8D` | View | botón de cinta | Scale to fit |  |  |
| `PROAPPCIN-B82B` | View | botón de cinta | Show labels |  |  |
| `PROAPPCIN-CBCA` | View | botón de cinta | Map image |  |  |

## Protocol ▸ Apparatus › pestaña «Zones»

Cómo llegar: pestaña Protocol → explorador «Apparatus» › pestaña «Zones»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPPPESZON-3A2C` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROAPPPESZON-B213` |  | texto | (sin etiqueta) |  | deshabilitado al inicio |
| `PROAPPPESZON-12F9` |  | botón | Zone colour: #94a3b8 (click to change) |  | deshabilitado al inicio |
| `PROAPPPESZON-AA34` |  | lista | — | valores: This is not a hidden zone ¦ This is a hidden zone | deshabilitado al inicio |
| `PROAPPPESZON-F2DA` |  | lista | Position of the zone remains the same in all tests, or can be different in each  | valores: Zone position: the same in all tests ¦ Zone position: can differ in each test | deshabilitado al inicio |
| `PROAPPPESZON-7CB0` |  | lista | When is the animal in this zone: by its centre, head or tail base, when a propor | valores: Zone entry: as in analysis settings ¦ Zone entry: centre of the animal ¦ Zone entry: the animal's head ¦ Zone entry: the animal's tail base ¦ Zone entry: part of the body in it ¦ Zone entry: not in any other zone | deshabilitado al inicio |
| `PROAPPPESZON-7796` |  | número | Investigation zone: the animal is in the zone while its head is within this dist |  | deshabilitado al inicio |
| `PROAPPPESZON-9715` |  | número | An entry only counts once the animal is oriented towards the zone: its body orie |  | deshabilitado al inicio |
| `PROAPPPESZON-C8DD` |  | número | Width of the zone's Whishaw's corridor, a band from the animal's start position  |  | deshabilitado al inicio |
| `PROAPPPESZON-E1D3` |  | botón | Duplicate |  | deshabilitado al inicio |
| `PROAPPPESZON-FC29` |  | botón | Use as arena |  | deshabilitado al inicio |
| `PROAPPPESZON-D305` |  | botón | Delete |  | deshabilitado al inicio |
| `PROAPPPESZON-E459` |  | botón | Select |  |  |
| `PROAPPPESZON-E994` |  | botón | Remove |  |  |

## Protocol ▸ Apparatus › pestaña «Points»

Cómo llegar: pestaña Protocol → explorador «Apparatus» › pestaña «Points»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPPPESPOI-E88C` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROAPPPESPOI-50FB` |  | texto | (sin etiqueta) |  | deshabilitado al inicio |
| `PROAPPPESPOI-145E` |  | botón | Point colour: #94a3b8 (click to change) |  | deshabilitado al inicio |
| `PROAPPPESPOI-E48B` |  | número | Distance counted as 'near' the point (object exploration, platform proximity) |  | deshabilitado al inicio |
| `PROAPPPESPOI-9710` |  | lista | As ANY-maze, a point can be located in each test by a heat map: where the animal | valores: Its position is set here, the same in all tests ¦ In each test, the hottest spot of the heat map of the animal's position ¦ In each test, the hottest spot of the heat map of where it froze ¦ In each test, the hottest spot of the heat map of where it was immobile ¦ In each test, the hottest spot of the heat map of where it reared | deshabilitado al inicio |
| `PROAPPPESPOI-AC6B` |  | número | (sin etiqueta) |  | deshabilitado al inicio |
| `PROAPPPESPOI-AC6B2` |  | número | (sin etiqueta) |  | deshabilitado al inicio |
| `PROAPPPESPOI-3998` |  | botón | Delete point |  | deshabilitado al inicio |

## Protocol ▸ Apparatus › pestaña «Lines»

Cómo llegar: pestaña Protocol → explorador «Apparatus» › pestaña «Lines»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPPPESLIN-D582` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROAPPPESLIN-5F77` |  | texto | (sin etiqueta) |  | deshabilitado al inicio |
| `PROAPPPESLIN-022C` |  | botón | Line colour: #94a3b8 (click to change) |  | deshabilitado al inicio |
| `PROAPPPESLIN-EB09` |  | botón | Delete line |  | deshabilitado al inicio |

## Protocol ▸ Apparatus › pestaña «Groups»

Cómo llegar: pestaña Protocol → explorador «Apparatus» › pestaña «Groups»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPPPESGRO-AD16` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROAPPPESGRO-C84E` |  | botón | Add group |  |  |
| `PROAPPPESGRO-2BF4` |  | botón | Delete |  |  |
| `PROAPPPESGRO-FB59` |  | texto | (sin etiqueta) |  |  |
| `PROAPPPESGRO-4ECF` |  | tabla/lista | The group is made of these zones | columnas: 1 |  |
| `PROAPPPESGRO-4E63` |  | tabla/lista | …minus these zones | columnas: 1 |  |

## Protocol ▸ Apparatus › pestaña «Sequences»

Cómo llegar: pestaña Protocol → explorador «Apparatus» › pestaña «Sequences»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPPPESSEQ-DF59` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `PROAPPPESSEQ-3985` |  | botón | Add sequence |  |  |
| `PROAPPPESSEQ-34B1` |  | botón | Delete |  | deshabilitado al inicio |
| `PROAPPPESSEQ-54F3` |  | texto | (sin etiqueta) |  | deshabilitado al inicio |
| `PROAPPPESSEQ-7A79` |  | tabla/lista | The animal visits these zones in order | columnas: 1 | deshabilitado al inicio |
| `PROAPPPESSEQ-430A` |  | lista | (sin etiqueta) | valores: Arena ¦ Centre ¦ Corner 1 ¦ Corner 2 ¦ Corner 3 ¦ Corner 4 ¦ Corners ¦ Periphery | deshabilitado al inicio |
| `PROAPPPESSEQ-A4FA` |  | botón | Add step |  | deshabilitado al inicio |
| `PROAPPPESSEQ-8F2D` |  | botón | Up |  | deshabilitado al inicio |
| `PROAPPPESSEQ-1E8B` |  | botón | Down |  | deshabilitado al inicio |
| `PROAPPPESSEQ-111F` |  | botón | Remove |  | deshabilitado al inicio |
| `PROAPPPESSEQ-C83E` |  | lista | Can begin at any step: any rotation of the steps counts (e.g. ABC, BCA, CAB) | valores: It can begin at any step (rotations count) ¦ It must begin at the first step | deshabilitado al inicio |
| `PROAPPPESSEQ-430A2` |  | lista | (sin etiqueta) | valores: No other zones allowed between steps ¦ Other zones allowed between steps | deshabilitado al inicio |
| `PROAPPPESSEQ-6E21` |  | lista | Either direction: the reversed order also counts (e.g. CBA) | valores: Only in the order shown ¦ In either direction | deshabilitado al inicio |
| `PROAPPPESSEQ-5CBE` |  | lista | Overlapping (sliding window): A B C A contains A B C and B C A | valores: Sequences cannot overlap ¦ Sequences may overlap | deshabilitado al inicio |
| `PROAPPPESSEQ-430A3` |  | lista | (sin etiqueta) | valores: Complete on entering the last step ¦ Complete on leaving the last step ¦ Complete on returning to the first step | deshabilitado al inicio |
| `PROAPPPESSEQ-329E` |  | número | (sin etiqueta) |  | deshabilitado al inicio |

## Protocol ▸ Apparatus

Cómo llegar: pestaña Protocol → explorador «Apparatus»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROAPP-DDF2` |  | número | Pixels per centimetre. 0 = not calibrated (results in pixels). |  |  |
| `PROAPP-6C3C` |  | lista | The unit distances and speeds are reported in, for every apparatus of the experi | valores: Results in mm ¦ Results in cm ¦ Results in m |  |
| `PROAPP-B5E4` |  | botón | Calibrate with the ruler |  |  |
| `PROAPP-CFD1` |  | botón | Clear |  |  |
| `PROAPP-869C` |  | lista | Use a frame from the video of one of the experiment's tests | valores: Use a test's video… ¦ Test 1: C1 · C1.avi ¦ Test 2: C2 · C2.avi ¦ Test 3: C3 · C3.avi ¦ Test 4: A1 · A1.avi ¦ Test 5: A2 · A2.avi ¦ Test 6: A3 · A3.avi |  |
| `PROAPP-CB5A` |  | deslizador | Time in the video of the background frame |  |  |
| `PROAPP-429B` |  | número | Time in the video of the background frame |  |  |

## Experiment ▸ Experiment — CINTA

Cómo llegar: pestaña Experiment → explorador «Experiment»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `EXPEXPCIN-8116` | Experiment | botón de cinta | Experiment |  |  |
| `EXPEXPCIN-91CE` | Experiment | botón de cinta | View treatments |  |  |
| `EXPEXPCIN-B3C5` | Experiment | botón de cinta | View animals |  |  |
| `EXPEXPCIN-AB61` | Experiment | botón de cinta | Add animals |  |  |
| `EXPEXPCIN-92AA` | Experiment | botón de cinta | Delete animals |  | deshabilitado al inicio |
| `EXPEXPCIN-143B` | Experiment | botón de cinta | Reveal treatment coding |  |  |
| `EXPEXPCIN-F16C` | Experiment | botón de cinta | Import animals |  |  |
| `EXPEXPCIN-9A7B` | Experiment | botón de cinta | Import tests |  |  |
| `EXPEXPCIN-3CD5` | Animals | botón de cinta | Retire |  | deshabilitado al inicio |
| `EXPEXPCIN-3F8A` | Animals | botón de cinta | Weigh |  |  |
| `EXPEXPCIN-2FFC` | Animals | botón de cinta | Randomise treatments |  |  |
| `EXPEXPCIN-A6A4` | Animals | botón de cinta | Dose calculator |  |  |
| `EXPEXPCIN-9CA5` | Animals | botón de cinta | Training criteria |  |  |
| `EXPEXPCIN-435C` | Animals | botón de cinta | Export CSV |  |  |
| `EXPEXPCIN-135F` | Animals | botón de cinta | Add animal |  |  |
| `EXPEXPCIN-D259` | Animals | botón de cinta | Duplicate |  | deshabilitado al inicio |
| `EXPEXPCIN-EB48` | Fields | botón de cinta | Add field |  |  |
| `EXPEXPCIN-215B` | Fields | botón de cinta | Rename field |  | deshabilitado al inicio |
| `EXPEXPCIN-0781` | Fields | botón de cinta | Remove field |  | deshabilitado al inicio |
| `EXPEXPCIN-4F4E` |  | botón de cinta | No user | menú: No user ¦ New user… ¦ Set password… ¦ Users and security… ¦ Remove a user from this experiment… |  |

## Experiment ▸ Experiment

Cómo llegar: pestaña Experiment → explorador «Experiment»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `EXPEXP-8BBE` |  | tabla/lista | (sin etiqueta) | columnas: Animal ¦ Animal ID ¦ Status ¦ Treatment ¦ Sex ¦ Tests ¦ Notes |  |

## Experiment ▸ Experiment ▸ Treatments — CINTA

Cómo llegar: pestaña Experiment → explorador «Experiment» → «Treatments»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `EXPEXPTRECIN-F1D5` | Treatments | botón de cinta | Add treatment |  |  |
| `EXPEXPTRECIN-7212` | Treatments | botón de cinta | Rename |  | deshabilitado al inicio |
| `EXPEXPTRECIN-2EE5` | Treatments | botón de cinta | Colour |  | deshabilitado al inicio |
| `EXPEXPTRECIN-53C4` | Treatments | botón de cinta | Delete treatment |  | deshabilitado al inicio |

## Experiment ▸ Experiment ▸ Treatments

Cómo llegar: pestaña Experiment → explorador «Experiment» → «Treatments»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `EXPEXPTRE-5F82` |  | tabla/lista | (sin etiqueta) | columnas: Treatment ¦ Code ¦ Colour ¦ Number of animals |  |

## Test ▸ Test schedule — CINTA

Cómo llegar: pestaña Test → explorador «Test schedule»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESTESSCHCIN-32F3` | Test | botón de cinta | Test schedule |  |  |
| `TESTESSCHCIN-B45F` | Test | botón de cinta | Run tests |  |  |
| `TESTESSCHCIN-4F2D` | Test | botón de cinta | Review and score |  |  |
| `TESTESSCHCIN-017D` | Tests | botón de cinta | Add tests from videos | menú: Add tests from videos… ¦ Join video files into one test… |  |
| `TESTESSCHCIN-A3F2` | Tests | botón de cinta | Add test |  |  |
| `TESTESSCHCIN-BC39` | Tests | botón de cinta | Schedule… | menú: Create schedule… ¦ Print schedule… ¦ Save schedule… ¦ Copy schedule |  |
| `TESTESSCHCIN-EDC2` | Tests | botón de cinta | Duplicate |  | deshabilitado al inicio |
| `TESTESSCHCIN-D20F` | Tests | botón de cinta | Set video file |  | deshabilitado al inicio |
| `TESTESSCHCIN-7BF7` | Tests | botón de cinta | Delete |  | deshabilitado al inicio |
| `TESTESSCHCIN-C141` | Testing | botón de cinta | Review test |  | deshabilitado al inicio |
| `TESTESSCHCIN-E998` | Testing | botón de cinta | Track selected |  | deshabilitado al inicio |
| `TESTESSCHCIN-CEFB` | Testing | botón de cinta | Track all untracked |  |  |
| `TESTESSCHCIN-0D6C` | Testing | botón de cinta | Import track |  | deshabilitado al inicio |
| `TESTESSCHCIN-06FE` | Testing | botón de cinta | Import track data |  | deshabilitado al inicio |
| `TESTESSCHCIN-02E1` | Testing | botón de cinta | Check video |  | deshabilitado al inicio |
| `TESTESSCHCIN-5240` | Status | botón de cinta | Skip |  | deshabilitado al inicio |
| `TESTESSCHCIN-C4AB` | Status | botón de cinta | Resume |  | deshabilitado al inicio |
| `TESTESSCHCIN-8B66` | Status | botón de cinta | Re-perform |  | deshabilitado al inicio |
| `TESTESSCHCIN-8D39` | Status | botón de cinta | Exclude |  | deshabilitado al inicio |
| `TESTESSCHCIN-B45F2` | Status | botón de cinta | Clear tracks |  | deshabilitado al inicio |
| `TESTESSCHCIN-23E1` | Status | botón de cinta | End stage for animal |  | deshabilitado al inicio |
| `TESTESSCHCIN-A189` | Status | botón de cinta | Reopen stage for animal |  | deshabilitado al inicio |
| `TESTESSCHCIN-CCA8` | Status | botón de cinta | Set user |  | deshabilitado al inicio |
| `TESTESSCHCIN-59F4` | Variables | botón de cinta | Test variables |  | deshabilitado al inicio |
| `TESTESSCHCIN-16A8` |  | botón de cinta | No user | menú: No user ¦ New user… ¦ Set password… ¦ Users and security… ¦ Remove a user from this experiment… |  |

## Test ▸ Test schedule

Cómo llegar: pestaña Test → explorador «Test schedule»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESTESSCH-CDCA` |  | tabla/lista | (sin etiqueta) | columnas: Test ¦ Animal ¦ Treatment ¦ Stage ¦ Trial ¦ Apparatus ¦ Video ¦ Testing status ¦ Start (s) ¦ Duration (s) ¦ User ¦ Notes |  |

## Test ▸ Run tests — CINTA

Cómo llegar: pestaña Test → explorador «Run tests»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESCIN-B7FC` | All apparatus | botón de cinta | Start all | menú: Start all now ¦ Arm all |  |
| `TESRUNTESCIN-573C` | All apparatus | botón de cinta | Stop all |  | deshabilitado al inicio |
| `TESRUNTESCIN-5D54` | All apparatus | botón de cinta | Pause all |  | deshabilitado al inicio |
| `TESRUNTESCIN-A82B` | All apparatus | botón de cinta | Resume all |  | deshabilitado al inicio |
| `TESRUNTESCIN-B7C9` | Session | botón de cinta | Add source | menú: Camera… ¦ Video file (simulated camera)… ¦ Scan for cameras ¦ Industrial cameras… |  |
| `TESRUNTESCIN-090A` | Session | botón de cinta | Add test panel |  |  |
| `TESRUNTESCIN-E1D8` | Session | botón de cinta | Remove panel |  | deshabilitado al inicio |
| `TESRUNTESCIN-EDB6` | Session | botón de cinta | Capture backgrounds |  |  |
| `TESRUNTESCIN-AA1F` | Session | botón de cinta | Camera image |  |  |
| `TESRUNTESCIN-42C3` | Session | botón de cinta | Camera options |  |  |
| `TESRUNTESCIN-9705` | Session | botón de cinta | Next test |  |  |
| `TESRUNTESCIN-7E83` | Session | botón de cinta | Adjust calibration |  | deshabilitado al inicio |
| `TESRUNTESCIN-D4E4` | Session | botón de cinta | Adjust apparatus |  | deshabilitado al inicio |
| `TESRUNTESCIN-BAF9` | View | botón de cinta | Apparatus layout | menú: One apparatus ¦ 2 × 1 ¦ 2 × 2 ¦ 3 × 2 ¦ 4 × 3 ¦ 5 × 4 ¦ 6 × 5 ¦ 8 × 5 (40 apparatus) ¦ 8 × 6 (48) ¦ Automatic (fit all) |  |
| `TESRUNTESCIN-C1CC` | View | botón de cinta | Scale to fit |  |  |
| `TESRUNTESCIN-F93F` | View | botón de cinta | Tracking indicators | menú: Animal's track (trail) ¦ Animal's orientation (flashlight beam) ¦ Highlight the zone the animal is in ¦ Zone names |  |
| `TESRUNTESCIN-4547` | View | botón de cinta | Hide report |  |  |
| `TESRUNTESCIN-F480` | View | botón de cinta | Hide apparatus |  |  |
| `TESRUNTESCIN-8035` | View | botón de cinta | Panel settings |  |  |
| `TESRUNTESCIN-B40A` | Mode | botón de cinta | One test |  |  |
| `TESRUNTESCIN-3B5E` | Mode | botón de cinta | Several tests |  |  |
| `TESRUNTESCIN-26C4` | Mode | botón de cinta | Observation only |  |  |

## Test ▸ Run tests › pestaña «Setup»

Cómo llegar: pestaña Test → explorador «Run tests» › pestaña «Setup»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESPES-A074` | Video source | opción | Use a camera |  |  |
| `TESRUNTESPES-D76D` | Video source | lista | (sin etiqueta) | valores: Camera 0 |  |
| `TESRUNTESPES-E512` | Video source | botón | Scan |  |  |
| `TESRUNTESPES-E301` | Video source | lista | Image size | valores: Camera default ¦ 640 × 480 ¦ 800 × 600 ¦ 1280 × 720 ¦ 1920 × 1080 |  |
| `TESRUNTESPES-BDA4` | Video source | número | Frame rate |  |  |
| `TESRUNTESPES-FB4D` | Video source | opción | Simulate a camera with a video file |  |  |
| `TESRUNTESPES-E3BF` | Video source | texto | No file chosen |  | deshabilitado al inicio |
| `TESRUNTESPES-769B` | Video source | botón | Choose… |  | deshabilitado al inicio |
| `TESRUNTESPES-4B2F` | Video source | lista | Play it at | valores: 1× real time ¦ 2× real time ¦ 4× real time ¦ 8× real time | deshabilitado al inicio |
| `TESRUNTESPES-2F74` | Test | lista | Test to run | valores: New test |  |
| `TESRUNTESPES-E3A8` | Test | lista | Animal | valores: C1 ¦ C2 ¦ C3 ¦ A1 ¦ A2 ¦ A3 |  |
| `TESRUNTESPES-5FBE` | Test | lista | Stage | valores: Day 1 |  |
| `TESRUNTESPES-8ADF` | Test | número | Trial |  |  |
| `TESRUNTESPES-BA94` | Test | lista | Apparatus | valores: Open field |  |
| `TESRUNTESPES-8A68` | Start and end | número | Test duration |  |  |
| `TESRUNTESPES-75C4` | Start and end | lista | The test starts | valores: Immediately when armed ¦ When the animal is detected ¦ When the experimenter leaves the view ¦ On a start key (keyboard / remote) ¦ On a start switch (an input) ¦ At a clock time |  |
| `TESRUNTESPES-DBFA` | Start and end | número | (sin etiqueta) |  | deshabilitado al inicio |
| `TESRUNTESPES-03D3` | Start and end | casilla | every day |  | deshabilitado al inicio |
| `TESRUNTESPES-644F` | Start and end | texto | Start switch |  | deshabilitado al inicio |
| `TESRUNTESPES-4A5D` | Start and end | texto | Start keys |  |  |
| `TESRUNTESPES-9371` | Start and end | número | Delay after the start switch |  |  |
| `TESRUNTESPES-BD5E` | Start and end | texto | Stop keys |  |  |
| `TESRUNTESPES-9F0A` | Start and end | texto | Test control input |  |  |
| `TESRUNTESPES-B73B` | Detection and recording | lista | Background | valores: Empty-arena image ¦ Adaptive (learns while running) |  |
| `TESRUNTESPES-AD75` | Detection and recording | casilla | Record a video of the test |  |  |
| `TESRUNTESPES-EB55` | Detection and recording | casilla | Burn the time and events into the video |  |  |
| `TESRUNTESPES-319E` | Detection and recording | número | Start a new video file every |  |  |
| `TESRUNTESPES-66F6` | Detection and recording | botón | File names… |  |  |
| `TESRUNTESPES-6BF3` | Detection and recording | número | Warn if the animal is lost for |  |  |
| `TESRUNTESPES-A60F` | Detection and recording | casilla | Switch all outputs off while a test is paused |  |  |
| `TESRUNTESPES-74D4` | Detection and recording | lista | Serial port |  |  |

## Test ▸ Run tests › pestaña «Monitor»

Cómo llegar: pestaña Test → explorador «Run tests» › pestaña «Monitor»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESPES-B1FB` |  | tabla/lista | No live test | columnas: Zone ¦ Time (s) ¦ Entries ¦ Latency (s) |  |
| `TESRUNTESPES-AA8D` |  | lista | (sin etiqueta) | valores: Speed ¦ Distance ¦ Motion (% body) ¦ Detected ¦ Freezing |  |
| `TESRUNTESPES-AA8D2` |  | lista | (sin etiqueta) | valores: last 30 s ¦ last 60 s ¦ last 120 s ¦ last 300 s |  |
| `TESRUNTESPES-E354` | Warnings | tabla/lista | (sin etiqueta) | columnas: 1 |  |

## Test ▸ Run tests › pestaña «Procedures»

Cómo llegar: pestaña Test → explorador «Run tests» › pestaña «Procedures»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESPES-F378` |  | botón | Functions… |  |  |
| `TESRUNTESPES-59AF` |  | botón | Add ▾ | menú: When ¦ Wait ¦ If ¦ Repeat ¦ Set ¦ Do ¦ Stop ¦ Comment ¦ Variable ¦ Call sub-procedure ¦ Label ¦ Go to ¦ Set timer resolution |  |
| `TESRUNTESPES-B3C7` |  | botón | Add inside ▾ | menú: When ¦ Wait ¦ If ¦ Repeat ¦ Set ¦ Do ¦ Stop ¦ Comment ¦ Variable ¦ Call sub-procedure ¦ Label ¦ Go to ¦ Set timer resolution | deshabilitado al inicio |
| `TESRUNTESPES-B526` |  | botón | Remove |  | deshabilitado al inicio |
| `TESRUNTESPES-6F29` |  | botón | Copy |  | deshabilitado al inicio |
| `TESRUNTESPES-658C` |  | botón | ▲ |  | deshabilitado al inicio |
| `TESRUNTESPES-5FE3` |  | botón | ▼ |  | deshabilitado al inicio |
| `TESRUNTESPES-13BA` |  | botón | → |  | deshabilitado al inicio |
| `TESRUNTESPES-228F` |  | botón | ← |  | deshabilitado al inicio |
| `TESRUNTESPES-9332` |  | botón | On/off |  | deshabilitado al inicio |
| `TESRUNTESPES-A4C8` |  | botón | JSON |  | deshabilitado al inicio |
| `TESRUNTESPES-EFE7` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `TESRUNTESPES-CABA` |  | tabla/lista | Problems found while checking the procedures; click one to select it | columnas: 1 |  |
| `TESRUNTESPES-59B9` |  | tabla/lista | <b>Procedures</b> | columnas: 1 |  |
| `TESRUNTESPES-74C0` |  | botón | Add | menú: Empty procedure ¦ From an example |  |
| `TESRUNTESPES-AF39` |  | botón | Duplicate |  | deshabilitado al inicio |
| `TESRUNTESPES-B5262` |  | botón | Remove |  | deshabilitado al inicio |
| `TESRUNTESPES-658C2` |  | botón | ▲ |  | deshabilitado al inicio |
| `TESRUNTESPES-5FE32` |  | botón | ▼ |  | deshabilitado al inicio |
| `TESRUNTESPES-6656` |  | casilla | Sub-procedure |  | deshabilitado al inicio |
| `TESRUNTESPES-DF92` |  | casilla | ANY-maze maths |  | deshabilitado al inicio |

## Test ▸ Run tests › pestaña «Results»

Cómo llegar: pestaña Test → explorador «Run tests» › pestaña «Results»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESPES-4369` |  | tabla/lista | The results of the last test appear here. | columnas: Measure ¦ Value |  |
| `TESRUNTESPES-34EA` |  | botón | Open in Review and score |  | deshabilitado al inicio |
| `TESRUNTESPES-9056` |  | botón | Next test |  |  |

## Test ▸ Run tests › pestaña «Log»

Cómo llegar: pestaña Test → explorador «Run tests» › pestaña «Log»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESPES-BE85` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `TESRUNTESPES-B7F7` |  | botón | Clear |  |  |

## Test ▸ Run tests

Cómo llegar: pestaña Test → explorador «Run tests»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTES-F2A3` |  | botón | Arm / Start test | menú: Start now ¦ Arm and wait for the start condition |  |
| `TESRUNTES-D2A1` |  | botón | Pause |  | deshabilitado al inicio |
| `TESRUNTES-3DFB` |  | botón | Stop |  | deshabilitado al inicio |
| `TESRUNTES-D7FF` |  | botón | Undo the last scored event |  | deshabilitado al inicio |
| `TESRUNTES-AD18` |  | botón | Show the camera image (preview) |  |  |
| `TESRUNTES-48C8` |  | botón | Capture the empty-arena background |  |  |
| `TESRUNTES-E106` |  | botón | Next test |  |  |
| `TESRUNTES-F61C` |  | botón | Options of this test | menú: Camera options ¦ Capture backgrounds ¦ Panel settings |  |
| `TESRUNTES-4D24` |  | deslizador | 00:00.00 |  |  |
| `TESRUNTES-3B3F` |  | botón | Rearing [R] toggle |  |  |
| `TESRUNTES-C095` |  | botón | Grooming [G] toggle |  |  |
| `TESRUNTES-92A7` |  | botón | Defecation [D] point |  |  |

## Test ▸ Run tests ▸ modo «Several tests» › pestaña «Setup»

Cómo llegar: pestaña Test → «Run tests» → botón de cinta «Several tests» › pestaña «Setup»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESMOD-4D91` | Test panels | tabla/lista | (sin etiqueta) | columnas: Source ¦ Apparatus ¦ Animal ¦ Stage ¦ Trial ¦ State ¦ Time |  |
| `TESRUNTESMOD-1094` | Test panels | lista | Camera / video |  | deshabilitado al inicio |
| `TESRUNTESMOD-D71F` | Test panels | lista | Apparatus | valores: Open field | deshabilitado al inicio |
| `TESRUNTESMOD-B606` | Test panels | lista | Animal | valores: C1 ¦ C2 ¦ C3 ¦ A1 ¦ A2 ¦ A3 | deshabilitado al inicio |
| `TESRUNTESMOD-5E43` | Test panels | lista | Stage | valores: Day 1 | deshabilitado al inicio |
| `TESRUNTESMOD-7650` | Test panels | número | Trial |  | deshabilitado al inicio |
| `TESRUNTESMOD-9695` | Test panels | lista | I/O device | valores: Automatic (only test running) ¦ None (simulated outputs) | deshabilitado al inicio |

## Test ▸ Run tests ▸ modo «Observation only»

Cómo llegar: pestaña Test → «Run tests» → botón de cinta «Observation only»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESRUNTESMOD-D672` |  | botón | Start observation |  |  |
| `TESRUNTESMOD-0684` |  | botón | Pause |  | deshabilitado al inicio |
| `TESRUNTESMOD-29A3` |  | botón | Stop and save |  | deshabilitado al inicio |
| `TESRUNTESMOD-40AC` |  | botón | Undo |  | deshabilitado al inicio |
| `TESRUNTESMOD-2A47` |  | botón | Rearing [R] toggle |  |  |
| `TESRUNTESMOD-101E` |  | botón | Grooming [G] toggle |  |  |
| `TESRUNTESMOD-97F8` |  | botón | Defecation [D] point |  |  |
| `TESRUNTESMOD-8D43` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |

## Test ▸ Review and score — CINTA

Cómo llegar: pestaña Test → explorador «Review and score»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-006A` | Test | botón de cinta | Previous test |  |  |
| `TESREVANDSCO-AA25` | Test | botón de cinta | Next test |  |  |
| `TESREVANDSCO-CD5E` | Test | botón de cinta | Track this test |  |  |
| `TESREVANDSCO-8948` | Playback | botón de cinta | Play |  |  |
| `TESREVANDSCO-4E92` | Playback | botón de cinta | Pause |  |  |
| `TESREVANDSCO-0276` | Playback | botón de cinta | Stop |  |  |
| `TESREVANDSCO-A3E8` | Playback | botón de cinta | Step back |  |  |
| `TESREVANDSCO-8A78` | Playback | botón de cinta | Step forward |  |  |
| `TESREVANDSCO-702B` | Playback | botón de cinta | Speed 1× | menú: 0.25× ¦ 0.5× ¦ 1× ¦ 2× ¦ 4× ¦ 8× |  |
| `TESREVANDSCO-6374` | Scoring | botón de cinta | On-screen keys |  |  |
| `TESREVANDSCO-9919` | Scoring | botón de cinta | Start |  | deshabilitado al inicio |
| `TESREVANDSCO-5DBB` | Scoring | botón de cinta | Pause |  | deshabilitado al inicio |
| `TESREVANDSCO-F291` | Scoring | botón de cinta | Stop |  | deshabilitado al inicio |
| `TESREVANDSCO-56D2` | View | botón de cinta | Zones |  |  |
| `TESREVANDSCO-4F12` | View | botón de cinta | Animal |  |  |
| `TESREVANDSCO-C73C` | View | botón de cinta | Trail |  |  |
| `TESREVANDSCO-D214` | View | botón de cinta | Detection preview |  |  |
| `TESREVANDSCO-B095` | Track editing | botón de cinta | Mark position |  |  |
| `TESREVANDSCO-B74D` | Track editing | botón de cinta | Interpolate |  |  |
| `TESREVANDSCO-384B` | Track editing | botón de cinta | Delete range |  |  |
| `TESREVANDSCO-E4CA` | Track editing | botón de cinta | Undo |  |  |
| `TESREVANDSCO-CDC2` | Track editing | botón de cinta | Remove jumps |  |  |

## Test ▸ Review and score › pestaña «Results»

Cómo llegar: pestaña Test → explorador «Review and score» › pestaña «Results»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-5C91` |  | texto | Filter measures… |  |  |
| `TESREVANDSCO-B7F2` |  | botón | Copy table |  |  |
| `TESREVANDSCO-877A` |  | tabla/lista | (sin etiqueta) | columnas: Measure ¦ Value |  |

## Test ▸ Review and score › pestaña «Plots»

Cómo llegar: pestaña Test → explorador «Review and score» › pestaña «Plots»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-9E60` |  | lista | (sin etiqueta) | valores: C1 |  |

## Test ▸ Review and score › pestaña «Scoring»

Cómo llegar: pestaña Test → explorador «Review and score» › pestaña «Scoring»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-C53E` |  | botón | Rearing [R] toggle |  |  |
| `TESREVANDSCO-5D42` |  | botón | Grooming [G] toggle |  |  |
| `TESREVANDSCO-A4D2` |  | botón | Defecation [D] point |  |  |
| `TESREVANDSCO-3E5F` |  | tabla/lista | (sin etiqueta) | columnas: Behaviour ¦ Start (s) ¦ End (s) ¦ Duration (s) |  |
| `TESREVANDSCO-CECE` |  | botón | Delete selected |  |  |
| `TESREVANDSCO-C0A8` |  | botón | Clear all |  |  |
| `TESREVANDSCO-0A25` |  | texto largo | Notes |  |  |

## Test ▸ Review and score › pestaña «Detection»

Cómo llegar: pestaña Test → explorador «Review and score» › pestaña «Detection»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-39E6` |  | lista | Detect the animal using | valores: Background subtraction ¦ A grey-level threshold ¦ Its colour (or a colour mark) |  |
| `TESREVANDSCO-A0A8` |  | lista | Compared with the background the animal is | valores: Darker or lighter ¦ Darker ¦ Lighter |  |
| `TESREVANDSCO-F1EB` |  | número | Detection threshold (0 = automatic) |  |  |
| `TESREVANDSCO-5BD6` |  | lista | Build the background from | valores: The median of sampled frames ¦ An empty-arena frame ¦ An adaptive model |  |
| `TESREVANDSCO-D6A1` |  | número | Empty-arena frame number |  |  |
| `TESREVANDSCO-55BF` |  | número | Frames sampled for the median |  |  |
| `TESREVANDSCO-2F5E` |  | casilla | Ignore lighting changes |  |  |
| `TESREVANDSCO-14B0` |  | texto | Colour of the animal or mark (#rrggbb) |  |  |
| `TESREVANDSCO-6C1F` |  | número | Colour tolerance (hue, degrees) |  |  |
| `TESREVANDSCO-5B91` |  | número | Ignore pixels greyer than (saturation 0–255) |  |  |
| `TESREVANDSCO-5EA1` |  | texto | Identify several animals by colour marks |  |  |
| `TESREVANDSCO-C2A9` |  | número | Ignore objects smaller than (px²) |  |  |
| `TESREVANDSCO-9CD4` |  | número | Ignore objects larger than (px², 0 = no limit) |  |  |
| `TESREVANDSCO-6026` |  | número | Blur the image by (px) |  |  |
| `TESREVANDSCO-8D6E` |  | número | Remove specks up to (px) |  |  |
| `TESREVANDSCO-CC73` |  | número | Fill holes up to (px) |  |  |
| `TESREVANDSCO-49CB` |  | número | Erase thin wires and bars up to (px) |  |  |
| `TESREVANDSCO-8BF7` |  | casilla | Detect the head and the tail |  |  |
| `TESREVANDSCO-0A50` |  | número | Tail removal strength |  |  |
| `TESREVANDSCO-D0CF` |  | casilla | Record the animal's whole-body outline |  |  |
| `TESREVANDSCO-895A` |  | lista | Locate the body parts using | valores: The animal's shape ¦ A pose model (AI) |  |
| `TESREVANDSCO-7E17` |  | número | Minimum keypoint confidence |  |  |
| `TESREVANDSCO-D4DB` |  | lista | Run the pose model on | valores: The fastest device available ¦ The CPU |  |
| `TESREVANDSCO-925D` |  | número | A pixel is moving when it changes by (grey levels) |  |  |
| `TESREVANDSCO-BD44` |  | lista | Count the pixel change of one animal in | valores: The whole arena ¦ The area around the animal |  |
| `TESREVANDSCO-CC6C` |  | número | Fill gaps in the track of up to (s) |  |  |
| `TESREVANDSCO-93E5` |  | número | Remove jumps faster than (units/s, 0 = off) |  |  |
| `TESREVANDSCO-5C71` |  | número | … when the track comes back within (s) |  |  |
| `TESREVANDSCO-ABF0` |  | casilla | Otherwise remove jumps faster than 300 cm/s (calibrated apparatus) |  |  |
| `TESREVANDSCO-B7E6` |  | número | Smooth positions over (frames, 0 = off) |  |  |
| `TESREVANDSCO-A80E` |  | número | Analyse every Nth frame |  |  |
| `TESREVANDSCO-52F9` |  | lista | Downscale the video before tracking | valores: No (full resolution) ¦ By 2 (half the width and height) ¦ By 4 (a quarter) |  |
| `TESREVANDSCO-BFB1` |  | lista | Set the detection up for | valores: Choose an animal… ¦ Mouse ¦ Rat ¦ Hooded rat ¦ White animal on sawdust ¦ Zebrafish |  |
| `TESREVANDSCO-3AC3` |  | botón | Reset to experiment defaults |  |  |
| `TESREVANDSCO-8569` |  | botón | Save as experiment default |  |  |
| `TESREVANDSCO-66AE` |  | botón | Apply to all tests |  |  |

## Test ▸ Review and score › pestaña «Track editing»

Cómo llegar: pestaña Test → explorador «Review and score» › pestaña «Track editing»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-4166` |  | lista | (sin etiqueta) | valores: C1 |  |
| `TESREVANDSCO-EB3F` | Mark position | botón | Mark position |  |  |
| `TESREVANDSCO-BF8F` | Mark position | número | (sin etiqueta) |  |  |
| `TESREVANDSCO-75DB` | Time range | botón | Range start |  |  |
| `TESREVANDSCO-5740` | Time range | botón | Range end |  |  |
| `TESREVANDSCO-21C3` | Time range | botón | Interpolate |  |  |
| `TESREVANDSCO-245E` | Time range | botón | Delete range |  |  |
| `TESREVANDSCO-FAFC` | Jumps | botón | Remove jumps |  |  |
| `TESREVANDSCO-75C9` | Apparatus position in this test | número | Move right |  |  |
| `TESREVANDSCO-10CB` | Apparatus position in this test | número | Move down |  |  |
| `TESREVANDSCO-ED9B` | Apparatus position in this test | número | Rotate |  |  |
| `TESREVANDSCO-5075` | Apparatus position in this test | número | Scale |  |  |
| `TESREVANDSCO-A514` | Apparatus position in this test | botón | Reset |  |  |
| `TESREVANDSCO-8345` |  | botón | Undo |  |  |

## Test ▸ Review and score

Cómo llegar: pestaña Test → explorador «Review and score»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `TESREVANDSCO-D051` |  | botón | Play |  |  |
| `TESREVANDSCO-C653` |  | botón | Pause |  |  |
| `TESREVANDSCO-454D` |  | botón | Stop |  |  |
| `TESREVANDSCO-1371` |  | botón | Step back |  |  |
| `TESREVANDSCO-B93C` |  | botón | Step forward |  |  |
| `TESREVANDSCO-0164` |  | botón | Speed 1× | menú: 0.25× ¦ 0.5× ¦ 1× ¦ 2× ¦ 4× ¦ 8× |  |
| `TESREVANDSCO-9943` |  | botón | Undo |  |  |
| `TESREVANDSCO-200F` |  | botón | Mark position |  |  |
| `TESREVANDSCO-6AC1` |  | botón | Detection preview |  |  |
| `TESREVANDSCO-C496` |  | lista | Test shown | valores: 1: C1 · Day 1 · trial 1 [tracked] ¦ 2: C2 · Day 1 · trial 1 [tracked] ¦ 3: C3 · Day 1 · trial 1 [tracked] ¦ 4: A1 · Day 1 · trial 1 [tracked] ¦ 5: A2 · Day 1 · trial 1 [tracked] ¦ 6: A3 · Day 1 · trial 1 [tracked] |  |
| `TESREVANDSCO-BD70` |  | deslizador | (sin etiqueta) |  |  |
| `TESREVANDSCO-FE98` |  | número | Time in the video at which the test starts |  |  |
| `TESREVANDSCO-1356` |  | botón | = now |  |  |
| `TESREVANDSCO-AEB9` |  | número | Test duration. 0 = experiment default |  |  |
| `TESREVANDSCO-3483` |  | botón | End = now |  |  |
| `TESREVANDSCO-6357` |  | número | Length of the trail drawn behind the animal |  |  |

## Results ▸ Data — CINTA

Cómo llegar: pestaña Results → explorador «Data»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATCIN-E466` | Results | botón de cinta | Data |  |  |
| `RESDATCIN-555D` | Results | botón de cinta | Statistics |  |  |
| `RESDATCIN-8EE9` | Navigation | botón de cinta | Back |  | deshabilitado al inicio |
| `RESDATCIN-00D5` | Navigation | botón de cinta | Forward |  | deshabilitado al inicio |
| `RESDATCIN-E2B4` | Clipboard | botón de cinta | Copy |  |  |
| `RESDATCIN-811D` | Clipboard | botón de cinta | Copy selection |  |  |
| `RESDATCIN-D082` | Spreadsheet | botón de cinta | Print |  |  |
| `RESDATCIN-4DB7` | Spreadsheet | botón de cinta | Save | menú: CSV file… ¦ Tab-separated text… ¦ Excel workbook (.xlsx)… ¦ SYLK spreadsheet (.slk)… ¦ dBase table (.dbf)… ¦ Selected cells… ¦ One row per animal (stages / trials as columns)… ¦ Mean of each animal's trials per stage… ¦ Experiment as XML (with raw tracks)… ¦ Raw data per test (CSV)… ¦ Event log of the shown tests… |  |
| `RESDATCIN-73FC` | Spreadsheet | botón de cinta | HTML report |  |  |
| `RESDATCIN-EAAB` | Spreadsheet | botón de cinta | E-mail report |  |  |
| `RESDATCIN-A0C7` | Actions | botón de cinta | Select data |  |  |
| `RESDATCIN-65A7` | Actions | botón de cinta | View spreadsheet |  | deshabilitado al inicio |
| `RESDATCIN-B766` | Actions | botón de cinta | Clear settings |  |  |
| `RESDATCIN-A007` | Actions | botón de cinta | Set segment length |  |  |
| `RESDATCIN-EFC9` | Actions | botón de cinta | Recalculate |  |  |
| `RESDATCIN-EA8E` | Report | botón de cinta | Report | menú: New report… ¦ Save report ¦ Save report as… ¦ Delete report ¦ Default report (shown when the experiment is opened) |  |
| `RESDATCIN-44A4` |  | botón de cinta | No user | menú: No user ¦ New user… ¦ Set password… ¦ Users and security… ¦ Remove a user from this experiment… |  |

## Results ▸ Data

Cómo llegar: pestaña Results → explorador «Data»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDAT-A7C4` |  | tabla/lista | ResultsTable | columnas: Test ¦ Animal ¦ Treatment ¦ Stage ¦ Trial ¦ Apparatus ¦ Test duration (s) ¦ Detection (%) ¦ Time not detected (s) ¦ Centre positions recorded ¦ Head positions recorded ¦ Head tracked (% of tracked frames) ¦ Tracking quality (%) ¦ Implausible jumps ¦ Total distance (cm) ¦ Mean speed (cm/s) ¦ Max speed (cm/s) ¦ Time mobile (s) ¦ Time immobile (s) ¦ Immobile episodes ¦ Latency to first immobility (s) ¦ Mean speed while mobile (cm/s) ¦ Mobile episodes ¦ Mean mobile episode (s) ¦ Mean immobile episode (s) ¦ Longest immobile episode (s) ¦ Shortest immobile episode (s) ¦ Longest mobile episode (s) ¦ Shortest mobile episode (s) ¦ Latency to first mobile episode (s) |  |

## Results ▸ Data ▸ Track plots — CINTA

Cómo llegar: pestaña Results → explorador «Data» → «Track plots»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATTRAPLO-D27B` | Figure | botón de cinta | Save figure |  |  |
| `RESDATTRAPLO-AFA7` | Figure | botón de cinta | Copy |  |  |
| `RESDATTRAPLO-44EF` | Figure | botón de cinta | Save playback |  |  |
| `RESDATTRAPLO-E813` | Test | botón de cinta | Open test |  |  |
| `RESDATTRAPLO-3435` | Test | botón de cinta | Export video |  |  |

## Results ▸ Data ▸ Track plots

Cómo llegar: pestaña Results → explorador «Data» → «Track plots»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATTRAPLO-9D87` |  | tabla/lista | TestList | columnas: 1 |  |
| `RESDATTRAPLO-E32F` |  | botón | Track |  |  |
| `RESDATTRAPLO-BB0E` |  | botón | Speed |  |  |
| `RESDATTRAPLO-3924` |  | botón | Play the track |  |  |
| `RESDATTRAPLO-0DD2` |  | lista | Playback speed (× real time) | valores: 0.25× ¦ 0.5× ¦ 1× ¦ 2× ¦ 4× ¦ 8× ¦ 16× |  |
| `RESDATTRAPLO-635A` |  | lista | Trail: show the whole track up to the current moment, or only its last seconds | valores: Whole track ¦ Last 2 s ¦ Last 5 s ¦ Last 10 s ¦ Last 30 s ¦ Last 60 s |  |
| `RESDATTRAPLO-32FE` |  | deslizador | Time in the test: drag to show the track up to that moment |  |  |

## Results ▸ Data ▸ Heat maps — CINTA

Cómo llegar: pestaña Results → explorador «Data» → «Heat maps»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATHEAMAP-D846` | Treatments | botón de cinta | Treatment heat maps |  |  |
| `RESDATHEAMAP-A847` | Hottest spot | botón de cinta | Add point here |  |  |

## Results ▸ Data ▸ Charts — CINTA

Cómo llegar: pestaña Results → explorador «Data» → «Charts»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATCHACIN-4623` | Chart | botón de cinta | Measure interval |  |  |
| `RESDATCHACIN-9962` | Chart | botón de cinta | Find peaks |  |  |
| `RESDATCHACIN-43FF` | Figure | botón de cinta | Export data |  |  |

## Results ▸ Data ▸ Charts

Cómo llegar: pestaña Results → explorador «Data» → «Charts»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATCHA-6835` |  | lista | Test | valores: Test 1 · C1 · Control ¦ Test 2 · C2 · Control ¦ Test 3 · C3 · Control ¦ Test 4 · A1 · Anxious ¦ Test 5 · A2 · Anxious ¦ Test 6 · A3 · Anxious |  |
| `RESDATCHA-F08F` |  | lista | Animal | valores: C1 | deshabilitado al inicio |
| `RESDATCHA-0565` |  | lista | Period | valores: Whole test ¦ 0-10 s ¦ 10-20 s ¦ 20-30 s |  |
| `RESDATCHA-33BC` |  | texto | <b>Parameters</b> (up to 10) |  |  |
| `RESDATCHA-A4E6` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `RESDATCHA-22A5` |  | tabla/lista | <b>Zone occupancy bands</b> | columnas: 1 |  |
| `RESDATCHA-99E7` |  | casilla | Show scored events |  |  |
| `RESDATCHA-3FCD` |  | botón | Home |  |  |
| `RESDATCHA-6F92` |  | botón | Back |  | deshabilitado al inicio |
| `RESDATCHA-61C9` |  | botón | Forward |  | deshabilitado al inicio |
| `RESDATCHA-1208` |  | botón | Pan |  |  |
| `RESDATCHA-42C2` |  | botón | Zoom |  |  |
| `RESDATCHA-DAFA` |  | botón | Subplots |  |  |
| `RESDATCHA-D2BD` |  | botón | Customize |  |  |
| `RESDATCHA-202B` |  | botón | Save |  |  |
| `RESDATCHA-01E2` |  | botón | Reset zoom |  | deshabilitado al inicio |
| `RESDATCHA-A4E62` |  | tabla/lista | (sin etiqueta) | columnas: Parameter ¦ From (s) ¦ To (s) ¦ Mean ¦ SD ¦ Min ¦ Max ¦ Change |  |

## Results ▸ Data ▸ Video export — CINTA

Cómo llegar: pestaña Results → explorador «Data» → «Video export»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATVIDEXP-DA5D` | Video | botón de cinta | Export video |  |  |
| `RESDATVIDEXP-938F` | Video | botón de cinta | Open test |  |  |

## Results ▸ Data ▸ Video export

Cómo llegar: pestaña Results → explorador «Data» → «Video export»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESDATVIDEXP-3B0C` |  | casilla | Draw the zones, points and lines |  |  |
| `RESDATVIDEXP-77CA` |  | casilla | Mark the centre, head and tail |  |  |
| `RESDATVIDEXP-C0AA` |  | casilla | Show scored behaviours and freezing |  |  |
| `RESDATVIDEXP-AAB2` |  | casilla | Add a time stamp and the test caption |  |  |
| `RESDATVIDEXP-2E76` |  | lista | Draw the track of the last | valores: - No track - ¦ 2 seconds ¦ 5 seconds ¦ 15 seconds ¦ Whole test so far |  |
| `RESDATVIDEXP-59F6` |  | lista | Colour the track by | valores: Speed ¦ Time ¦ Animal colour |  |
| `RESDATVIDEXP-D1F5` |  | lista | Playback speed of the video | valores: 0.5× ¦ 1× ¦ 2× ¦ 4× ¦ 8× |  |
| `RESDATVIDEXP-B8E8` |  | lista | Size of the video | valores: 100 % ¦ 75 % ¦ 50 % |  |
| `RESDATVIDEXP-BEBE` |  | botón | Export video… |  |  |

## Results ▸ Statistics — CINTA

Cómo llegar: pestaña Results → explorador «Statistics»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESSTACIN-9ACE` | Analysis | botón de cinta | Run |  |  |
| `RESSTACIN-387C` | Analysis | botón de cinta | Recalculate |  |  |
| `RESSTACIN-1321` | Report | botón de cinta | Copy summary |  |  |
| `RESSTACIN-6835` | Report | botón de cinta | Save figure |  |  |
| `RESSTACIN-D337` | Report | botón de cinta | Save report |  |  |
| `RESSTACIN-FB07` | Report | botón de cinta | Copy figure |  |  |
| `RESSTACIN-4864` | Report | botón de cinta | Copy table |  | deshabilitado al inicio |
| `RESSTACIN-5128` | Report | botón de cinta | Save table |  | deshabilitado al inicio |

## Results ▸ Statistics

Cómo llegar: pestaña Results → explorador «Statistics»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESSTA-3E6F` |  | lista | Select the measure to analyse | valores: Test duration (s) ¦ Detection (%) ¦ Time not detected (s) ¦ Centre positions recorded ¦ Head positions recorded ¦ Head tracked (% of tracked frames) ¦ Tracking quality (%) ¦ Implausible jumps ¦ Total distance (cm) ¦ Mean speed (cm/s) ¦ Max speed (cm/s) ¦ Time mobile (s) ¦ Time immobile (s) ¦ Immobile episodes ¦ Latency to first immobility (s) ¦ Mean speed while mobile (cm/s) ¦ Mobile episodes ¦ Mean mobile episode (s) ¦ Mean immobile episode (s) ¦ Longest immobile episode (s) ¦ Shortest immobile episode (s) ¦ Longest mobile episode (s) ¦ Shortest mobile episode (s) ¦ Latency to first mobile episode (s) ¦ Latency to last mobile episode (s) ¦ …(+510) |  |
| `RESSTA-DE1D` |  | lista | Select the independent variable | valores: Treatment ¦ Stage ¦ Period |  |
| `RESSTA-C821` |  | casilla | Repeated measures |  |  |
| `RESSTA-E7B1` |  | lista | Select the time period to analyse | valores: Whole test ¦ 0-10 s ¦ 10-20 s ¦ 20-30 s |  |
| `RESSTA-C7C6` |  | lista | (sin etiqueta) | valores: - All tests - ¦ Treatment ¦ Stage |  |
| `RESSTA-CCF4` |  | lista | = |  | deshabilitado al inicio |
| `RESSTA-9F61` |  | número | Select the significance level (α) |  |  |
| `RESSTA-32BA` |  | opción | Parametric |  |  |
| `RESSTA-4E0E` |  | opción | Non-parametric |  |  |
| `RESSTA-B75F` |  | lista | Optionally select a specific statistical test | valores: Automatic ¦ Student's t-test ¦ Welch's t-test ¦ Mann-Whitney U ¦ Kolmogorov-Smirnov ¦ Brunner-Munzel ¦ Paired t-test ¦ Wilcoxon signed-rank ¦ One-way ANOVA ¦ Welch's ANOVA ¦ Alexander-Govern ¦ Kruskal-Wallis ¦ Mood's median test ¦ Repeated-measures ANOVA ¦ Friedman ¦ One-sample t-test vs value ¦ Wilcoxon signed-rank vs value |  |
| `RESSTA-7F62` |  | lista | Optionally select a post-hoc test to use | valores: Automatic ¦ - None - ¦ Bonferroni test ¦ Duncan's test ¦ Fisher's LSD test ¦ Scheffé's test ¦ Šidák test ¦ Student-Newman-Keuls test ¦ Tukey test ¦ Holm test ¦ Benjamini-Hochberg (FDR) ¦ Dunnett test (vs control) ¦ Games-Howell test ¦ Dunn's test |  |
| `RESSTA-9114` |  | lista | Select the control group to compare to | valores: Control ¦ Anxious | deshabilitado al inicio |
| `RESSTA-7EBE` |  | número | Select the value to compare the measure to |  | deshabilitado al inicio |
| `RESSTA-64DF` |  | lista | Select the type of graph | valores: Column (mean ± error) ¦ Points (mean ± error) ¦ Box plot ¦ Violin plot |  |
| `RESSTA-FAD1` |  | lista | Select what the error bars show | valores: SEM ¦ SD ¦ 95% CI |  |
| `RESSTA-3AE0` |  | casilla | Show individual values |  |  |
| `RESSTA-CFBB` |  | tabla/lista | Descriptive statistics | columnas: Treatment ¦ n ¦ Mean ¦ SD ¦ SEM ¦ 95% CI ¦ Median ¦ Min ¦ Max |  |
| `RESSTA-B2AC` |  | tabla/lista | Assumption checks | columnas: Check ¦ Group ¦ p |  |

## Results ▸ Statistics ▸ Two factors

Cómo llegar: pestaña Results → explorador «Statistics» → «Two factors»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESSTATWOFAC-C469` |  | lista | Select the 1st independent variable | valores: Stage ¦ Period |  |
| `RESSTATWOFAC-16A6` |  | lista | Optionally select a 2nd independent variable | valores: Treatment ¦ Stage ¦ - None - |  |
| `RESSTATWOFAC-947A` |  | lista | Select the design of the analysis | valores: Between subjects (two-way ANOVA) ¦ Repeated on X axis (mixed / RM ANOVA) ¦ Non-parametric (Scheirer-Ray-Hare) ¦ Aligned rank transform ANOVA |  |
| `RESSTATWOFAC-BD9B` |  | lista | Select the type of graph | valores: Line (mean ± error) ¦ Column (mean ± error) ¦ Points (mean ± error) ¦ Box plot ¦ Violin plot |  |
| `RESSTATWOFAC-90A8` |  | tabla/lista | Analysis of variance | columnas: Effect ¦ SS ¦ df ¦ F ¦ p ¦ p (GG) |  |

## Results ▸ Statistics ▸ Correlation

Cómo llegar: pestaña Results → explorador «Statistics» → «Correlation»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESSTACOR-3DF2` |  | lista | Select the 1st measure (X axis) | valores: Test duration (s) ¦ Detection (%) ¦ Time not detected (s) ¦ Centre positions recorded ¦ Head positions recorded ¦ Head tracked (% of tracked frames) ¦ Tracking quality (%) ¦ Implausible jumps ¦ Total distance (cm) ¦ Mean speed (cm/s) ¦ Max speed (cm/s) ¦ Time mobile (s) ¦ Time immobile (s) ¦ Immobile episodes ¦ Latency to first immobility (s) ¦ Mean speed while mobile (cm/s) ¦ Mobile episodes ¦ Mean mobile episode (s) ¦ Mean immobile episode (s) ¦ Longest immobile episode (s) ¦ Shortest immobile episode (s) ¦ Longest mobile episode (s) ¦ Shortest mobile episode (s) ¦ Latency to first mobile episode (s) ¦ Latency to last mobile episode (s) ¦ …(+510) |  |
| `RESSTACOR-EDF9` |  | lista | Select the 2nd measure (Y axis) | valores: Test duration (s) ¦ Detection (%) ¦ Time not detected (s) ¦ Centre positions recorded ¦ Head positions recorded ¦ Head tracked (% of tracked frames) ¦ Tracking quality (%) ¦ Implausible jumps ¦ Total distance (cm) ¦ Mean speed (cm/s) ¦ Max speed (cm/s) ¦ Time mobile (s) ¦ Time immobile (s) ¦ Immobile episodes ¦ Latency to first immobility (s) ¦ Mean speed while mobile (cm/s) ¦ Mobile episodes ¦ Mean mobile episode (s) ¦ Mean immobile episode (s) ¦ Longest immobile episode (s) ¦ Shortest immobile episode (s) ¦ Longest mobile episode (s) ¦ Shortest mobile episode (s) ¦ Latency to first mobile episode (s) ¦ Latency to last mobile episode (s) ¦ …(+510) |  |
| `RESSTACOR-028C` |  | lista | Optionally colour the points by (and compare with ANCOVA) | valores: Treatment ¦ Stage ¦ - None - |  |
| `RESSTACOR-736F` |  | lista | Select the type of correlation | valores: Pearson ¦ Spearman ¦ Kendall |  |

## Results ▸ Statistics ▸ Grouped

Cómo llegar: pestaña Results → explorador «Statistics» → «Grouped»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESSTAGRO-77AA` |  | lista | Select the 1st independent variable | valores: Treatment ¦ Stage ¦ Period |  |
| `RESSTAGRO-25DE` |  | lista | Optionally select a 2nd independent variable | valores: - None - ¦ Treatment ¦ Stage ¦ Period |  |
| `RESSTAGRO-5B47` |  | lista | Optionally select a 3rd independent variable | valores: - None - ¦ Treatment ¦ Stage ¦ Period |  |
| `RESSTAGRO-5791` |  | tabla/lista | Descriptive statistics | columnas: Level ¦ n ¦ Mean ¦ SD ¦ SEM ¦ 95% CI ¦ Median ¦ Min ¦ Max |  |

## Results ▸ Statistics ▸ Categorical

Cómo llegar: pestaña Results → explorador «Statistics» → «Categorical»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `RESSTACAT-9731` |  | lista | Select the categorical result to analyse | valores: First zone entered ¦ Arena: was first zone entered ¦ Arena: visit durations (s) ¦ Centre: was first zone entered ¦ Centre: visit durations (s) ¦ Corner 1: was first zone entered ¦ Corner 1: visit durations (s) ¦ Corner 2: was first zone entered ¦ Corner 2: visit durations (s) ¦ Corner 3: was first zone entered ¦ Corner 3: visit durations (s) ¦ Corner 4: was first zone entered ¦ Corner 4: visit durations (s) ¦ Periphery: visit durations (s) ¦ Rearing: press durations (s) ¦ Treatment ¦ Stage |  |
| `RESSTACAT-70A3` |  | lista | Select the variable to group the tests by | valores: Treatment ¦ Stage |  |
| `RESSTACAT-5A63` |  | tabla/lista | Counts | columnas: Total |  |

## Modo «TakeNote» ▸ Review and score › pestaña «Scoring»

Cómo llegar: Protocol ▸ Mode = TakeNote → Review and score › pestaña «Scoring»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `MODTAKREVAND-BD4B` |  | botón | Rearing [R] toggle |  |  |
| `MODTAKREVAND-815A` |  | botón | Grooming [G] toggle |  |  |
| `MODTAKREVAND-44B8` |  | botón | Defecation [D] point |  |  |

## Modo «Input/output only» ▸ Review and score › pestaña «Scoring»

Cómo llegar: Protocol ▸ Mode = Input/output only → Review and score › pestaña «Scoring»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `MODINPOUTONL-A14F` |  | botón | Rearing [R] toggle |  |  |
| `MODINPOUTONL-57C7` |  | botón | Grooming [G] toggle |  |  |
| `MODINPOUTONL-9847` |  | botón | Defecation [D] point |  |  |

## Diálogo «New experiment»

Cómo llegar: menú File ▸ New experiment… → diálogo «New experiment»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DINEWEXP-EBC3` |  | texto | Name |  |  |
| `DINEWEXP-397A` |  | texto | (sin etiqueta) |  |  |
| `DINEWEXP-BF54` |  | botón | Choose… |  |  |
| `DINEWEXP-158E` |  | lista | Protocol | valores: Open field ¦ Elevated plus maze ¦ Elevated zero maze ¦ Y maze (spontaneous alternation) ¦ T maze ¦ Radial arm maze ¦ Radial arm place conditioning (RAPC) ¦ Morris water maze ¦ Barnes maze ¦ Novel object recognition ¦ Light/dark box ¦ Three-chamber sociability ¦ Fear conditioning / freezing ¦ Forced swim test (Porsolt) ¦ Tail suspension test ¦ Novel tank diving test (fish) ¦ Multi-well plate (larvae) ¦ Conditioned place preference ¦ Hole board ¦ Thermal gradient ring ¦ Home cage ¦ Activity wheel / circular runway ¦ Custom |  |
| `DINEWEXP-2BDF` |  | número | Test duration |  |  |
| `DINEWEXP-CB68` |  | texto | Apparatus, stages, keys, procedures, I/O devices, tracking and analysis settings |  |  |
| `DINEWEXP-BF542` |  | botón | Choose… |  |  |
| `DINEWEXP-4ED3` |  | casilla | Also copy the treatments |  | deshabilitado al inicio |
| `DINEWEXP-9EA1` |  | botón | OK |  |  |
| `DINEWEXP-7C06` |  | botón | Cancel |  |  |

## Diálogo «Users and security»

Cómo llegar: menú File ▸ Users and security… → diálogo «Users and security»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIUSEANDSEC-439B` |  | tabla/lista | (sin etiqueta) | columnas: User ¦ Role ¦ Password |  |
| `DIUSEANDSEC-FAD1` |  | botón | Set password… |  | deshabilitado al inicio |
| `DIUSEANDSEC-EFEE` |  | botón | Clear password |  | deshabilitado al inicio |
| `DIUSEANDSEC-222B` |  | botón | Make administrator |  | deshabilitado al inicio |
| `DIUSEANDSEC-322D` | Security | lista | Reveal treatment coding | valores: Anyone ¦ Administrators only | deshabilitado al inicio |
| `DIUSEANDSEC-3DDD` | Security | casilla | Lock the protocol: only administrators can change it |  | deshabilitado al inicio |
| `DIUSEANDSEC-C92C` |  | botón | Close |  |  |

## Diálogo «Protect experiment»

Cómo llegar: menú File ▸ Protect experiment… → diálogo «Protect experiment»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIPROEXP-C1FD` |  | texto | New password |  |  |
| `DIPROEXP-498B` |  | texto | Type it again |  |  |
| `DIPROEXP-866C` |  | botón | OK |  |  |
| `DIPROEXP-3BB4` |  | botón | Cancel |  |  |

## Diálogo «Allowed programs»

Cómo llegar: menú File ▸ Allowed programs… → diálogo «Allowed programs»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIALLPRO-992E` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DIALLPRO-F17A` |  | botón | Remove |  | deshabilitado al inicio |
| `DIALLPRO-57F1` |  | botón | Close |  |  |

## Diálogo «mANY-MAZE user guide»

Cómo llegar: menú Help ▸ User guide → diálogo «mANY-MAZE user guide»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIMANMAZUSEG-B27D` |  | texto largo | (sin etiqueta) |  |  |

## Diálogo «QMessageBox»

Cómo llegar: menú Help ▸ About mANY-MAZE → diálogo «QMessageBox»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIQME-BCFC` |  | botón | OK |  |  |

## Diálogo «I/O devices»

Cómo llegar: Protocol: cinta «I/O devices» → diálogo «I/O devices»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIIODEV-9ECF` | Device | texto | Name |  | deshabilitado al inicio |
| `DIIODEV-CD84` | Device | lista | Type | valores: Simulated device ¦ Arduino (mANY-MAZE I/O firmware) ¦ Serial port (text commands) ¦ Audio output (speakers) ¦ USB-serial cable control lines (TTL cable) ¦ Firmata board (StandardFirmata) ¦ National Instruments DAQ (NI-DAQmx) ¦ LabJack T4 / T7 / T8 (LJM) ¦ Alerts (e-mail / SMS) ¦ Syringe pump(s) ¦ Balance (animal / food weights) | deshabilitado al inicio |
| `DIIODEV-E89E` | Device | casilla | Enabled |  | deshabilitado al inicio |
| `DIIODEV-B519` | Channels | tabla/lista | Options (key=value, comma separated): pullup, debounce_ms, counts_per_rev, cm_pe | columnas: Name ¦ Kind ¦ Pin ¦ Pin B ¦ Invert ¦ On text ¦ Off text ¦ Options ¦ Role ¦ Max on (s) | deshabilitado al inicio |
| `DIIODEV-E64B` | Channels | botón | Add channel |  | deshabilitado al inicio |
| `DIIODEV-28D0` | Channels | botón | Remove channel |  | deshabilitado al inicio |
| `DIIODEV-A616` | Channels | botón | Calibrate… |  | deshabilitado al inicio |
| `DIIODEV-C4E7` |  | tabla/lista | <b>Devices</b> | columnas: 1 |  |
| `DIIODEV-F391` |  | botón | Add ▾ | menú: Simulated device ¦ Arduino (mANY-MAZE I/O firmware) ¦ Serial port (text commands) ¦ Audio output (speakers) ¦ USB-serial cable control lines (TTL cable) ¦ Firmata board (StandardFirmata) ¦ National Instruments DAQ (NI-DAQmx) ¦ LabJack T4 / T7 / T8 (LJM) ¦ Alerts (e-mail / SMS) ¦ Syringe pump(s) ¦ Balance (animal / food weights) ¦ Operant chamber (preset)… |  |
| `DIIODEV-B2E3` |  | botón | Remove |  |  |
| `DIIODEV-B2E8` | Live status | botón | Connect |  |  |
| `DIIODEV-DDA1` | Live status | botón | Test |  |  |
| `DIIODEV-2C54` | Live status | tabla/lista | (sin etiqueta) | columnas: Device ¦ Channel ¦ Kind ¦ Value |  |
| `DIIODEV-104C` |  | botón | OK |  |  |
| `DIIODEV-AD9A` |  | botón | Cancel |  |  |

## Diálogo «Touch screen»

Cómo llegar: Protocol: cinta «Touch screen» → diálogo «Touch screen»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DITOUSCR-2907` |  | casilla | Use a touch screen in live tests (one-test mode) |  |  |
| `DITOUSCR-9391` |  | lista | Show the stimuli on | valores: Display 1: (800×800) |  |
| `DITOUSCR-FD48` |  | número | Response windows in a row |  |  |
| `DITOUSCR-5077` |  | casilla | Outline the response windows |  |  |
| `DITOUSCR-5750` |  | botón | Show on screen for 3 s |  |  |
| `DITOUSCR-5694` |  | botón | OK |  |  |
| `DITOUSCR-AFA2` |  | botón | Cancel |  |  |

## Diálogo «Create apparatus from template»

Cómo llegar: Protocol: cinta «Apparatus from template» → diálogo «Create apparatus from template»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DICREAPPFROT-36F1` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DICREAPPFROT-258A` | Parameters | número | Size (cm) |  |  |
| `DICREAPPFROT-6E2E` | Parameters | número | Centre fraction |  |  |
| `DICREAPPFROT-98B2` | Parameters | casilla | Corners |  |  |
| `DICREAPPFROT-D7E3` | Parameters | casilla | Circular |  |  |
| `DICREAPPFROT-8D43` | Placement on the video image | opción | Drag a rectangle around the apparatus on the image |  |  |
| `DICREAPPFROT-5F39` | Placement on the video image | opción | Fit to the whole frame |  |  |
| `DICREAPPFROT-4F7F` | Placement on the video image | casilla | Keep square proportions |  |  |
| `DICREAPPFROT-7B73` | Apparatus | opción | Replace “Open field” |  |  |
| `DICREAPPFROT-1699` | Apparatus | opción | Add as a new apparatus |  |  |
| `DICREAPPFROT-BD9A` | Apparatus | texto | Name |  |  |
| `DICREAPPFROT-86FE` |  | botón | Create |  |  |
| `DICREAPPFROT-A4F0` |  | botón | Cancel |  |  |

## Diálogo «Expression functions»

Cómo llegar: Protocol ▸ Procedures: cinta «Functions» → diálogo «Expression functions»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIEXPFUN-A32E` |  | texto largo | (sin etiqueta) |  |  |

## Diálogo «Add grid»

Cómo llegar: Apparatus: cinta «Zone grid» → diálogo «Add grid»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIADDGRI-3EFA` |  | lista | Grid type | valores: Square cells ¦ Concentric rings ¦ Radial sectors ¦ Rings × sectors |  |
| `DIADDGRI-7C1F` |  | lista | Cover | valores: Arena boundary ¦ Selected zone ¦ Whole image |  |
| `DIADDGRI-9C1D` |  | texto | Name |  |  |
| `DIADDGRI-3164` |  | número | Columns |  |  |
| `DIADDGRI-7934` |  | número | Rows |  |  |
| `DIADDGRI-D906` |  | número | Cell size |  |  |
| `DIADDGRI-0639` |  | casilla | Clip square cells to the region |  |  |
| `DIADDGRI-B375` |  | casilla | Add a zone group containing all cells |  |  |
| `DIADDGRI-080F` |  | botón | Add grid |  |  |
| `DIADDGRI-B845` |  | botón | Cancel |  |  |

## Diálogo «Lens correction of the test videos»

Cómo llegar: Apparatus: cinta «Lens correction» → diálogo «Lens correction of the test videos»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DILENCOROFTH-9CCF` |  | casilla | Show the original image |  |  |
| `DILENCOROFTH-1626` |  | casilla | Straight guide lines |  |  |
| `DILENCOROFTH-1C92` |  | lista | Correction | valores: None ¦ Barrel strength (one slider) ¦ Checkerboard calibration |  |
| `DILENCOROFTH-0AA7` |  | casilla | Keep the whole image (black corners) |  | deshabilitado al inicio |
| `DILENCOROFTH-B0EE` |  | lista | Apply to | valores: Tests that use this video (1) ¦ Video tests of this apparatus (6) ¦ All the tests with a video (6) |  |
| `DILENCOROFTH-E3DF` |  | botón | OK |  |  |
| `DILENCOROFTH-2444` |  | botón | Cancel |  |  |

## Diálogo «Add animals»

Cómo llegar: Experiment: cinta «Add animals» → diálogo «Add animals»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIADDANI-4EDA` |  | texto | ID prefix |  |  |
| `DIADDANI-F19A` |  | número | Number of animals |  |  |
| `DIADDANI-5080` |  | número | First number |  |  |
| `DIADDANI-EE4B` |  | número | Digits |  |  |
| `DIADDANI-83EB` |  | lista | Treatment | valores: Control ¦ Anxious |  |
| `DIADDANI-9009` |  | botón | OK |  |  |
| `DIADDANI-125E` |  | botón | Cancel |  |  |

## Diálogo «Import animals»

Cómo llegar: Experiment: cinta «Import animals» → diálogo «Import animals»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIIMPANI-3C9E` |  | texto | (sin etiqueta) |  |  |
| `DIIMPANI-2626` |  | botón | Choose file… |  |  |
| `DIIMPANI-FF5F` |  | tabla/lista | (sin etiqueta) |  |  |
| `DIIMPANI-C1D9` |  | lista | Animal ID column |  |  |
| `DIIMPANI-604E` |  | lista | Treatment column |  |  |
| `DIIMPANI-1873` |  | lista | Sex column |  |  |
| `DIIMPANI-FDE7` |  | lista | Notes column |  |  |
| `DIIMPANI-EE81` |  | tabla/lista | Also import as animal fields | columnas: 1 |  |
| `DIIMPANI-5B56` |  | botón | Import |  |  |
| `DIIMPANI-6CEF` |  | botón | Cancel |  |  |

## Diálogo «Import tests (test schedule)»

Cómo llegar: Experiment: cinta «Import tests» → diálogo «Import tests (test schedule)»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIIMPTESTESS-D213` |  | texto | (sin etiqueta) |  |  |
| `DIIMPTESTESS-3913` |  | botón | Choose file… |  |  |
| `DIIMPTESTESS-074D` |  | tabla/lista | (sin etiqueta) |  |  |
| `DIIMPTESTESS-BD11` |  | lista | Test number column |  |  |
| `DIIMPTESTESS-F6E9` |  | lista | Animal ID column |  |  |
| `DIIMPTESTESS-85D7` |  | lista | Treatment column |  |  |
| `DIIMPTESTESS-B89A` |  | lista | Stage column |  |  |
| `DIIMPTESTESS-A4CB` |  | lista | Trial column |  |  |
| `DIIMPTESTESS-B3E2` |  | lista | Apparatus column |  |  |
| `DIIMPTESTESS-4BC3` |  | lista | Video file column |  |  |
| `DIIMPTESTESS-D585` |  | lista | Test duration column |  |  |
| `DIIMPTESTESS-2EC2` |  | texto | Video folder |  |  |
| `DIIMPTESTESS-2C2A` |  | botón | Import |  |  |
| `DIIMPTESTESS-6E58` |  | botón | Cancel |  |  |

## Diálogo «Weigh animals»

Cómo llegar: Experiment: cinta «Weigh» → diálogo «Weigh animals»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIWEIANI-FF94` |  | lista | Scale | valores: Type the weight |  |
| `DIWEIANI-FA7E` |  | número | (sin etiqueta) |  |  |
| `DIWEIANI-673B` |  | botón | Read scale |  | deshabilitado al inicio |
| `DIWEIANI-CFCB` |  | tabla/lista | Weight history | columnas: Date ¦ Weight (g) |  |
| `DIWEIANI-B550` |  | casilla | Read the scale for the next animal |  |  |
| `DIWEIANI-F0BB` |  | botón | Close |  |  |
| `DIWEIANI-A056` |  | botón | Record |  |  |
| `DIWEIANI-3226` |  | botón | Record next |  |  |

## Diálogo «Randomise treatments»

Cómo llegar: Experiment: cinta «Randomise treatments» → diálogo «Randomise treatments»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIRANTRE-E568` |  | tabla/lista | Treatments | columnas: 1 |  |
| `DIRANTRE-2337` |  | lista | Balance within | valores: - Nothing - ¦ Sex |  |
| `DIRANTRE-55D6` |  | número | Seed |  |  |
| `DIRANTRE-7AFB` |  | casilla | Only the 1 selected animal |  |  |
| `DIRANTRE-36CA` |  | botón | Allocate |  |  |
| `DIRANTRE-C107` |  | botón | Cancel |  |  |

## Diálogo «Dose calculator»

Cómo llegar: Experiment: cinta «Dose calculator» → diálogo «Dose calculator»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIDOSCAL-F0F5` |  | lista | Weight column | valores: Weight (g) |  |
| `DIDOSCAL-5D43` |  | número | Default dose |  |  |
| `DIDOSCAL-B49A` |  | número | Concentration |  |  |
| `DIDOSCAL-20DD` |  | casilla | Only the 1 selected animal |  |  |
| `DIDOSCAL-6CF6` |  | botón | Calculate volumes |  |  |
| `DIDOSCAL-B51D` |  | botón | Cancel |  |  |

## Diálogo «Add tests from videos»

Cómo llegar: Test schedule: cinta «Add tests from videos» ▸ «Add tests from videos…» → diálogo «Add tests from videos»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIADDTESFROV-51C3` |  | lista | Stage | valores: Day 1 |  |
| `DIADDTESFROV-CD92` |  | número | Trial |  |  |
| `DIADDTESFROV-B824` |  | lista | Apparatus | valores: Open field |  |
| `DIADDTESFROV-B870` |  | casilla | One test per apparatus (several arenas filmed in each video) |  | deshabilitado al inicio |
| `DIADDTESFROV-1281` |  | tabla/lista | Animal IDs (new IDs are added to the animal list) | columnas: Video ¦ Apparatus ¦ Animal ID |  |
| `DIADDTESFROV-A830` |  | botón | OK |  |  |
| `DIADDTESFROV-CFC8` |  | botón | Cancel |  |  |

## Diálogo «Create test schedule»

Cómo llegar: Test schedule: cinta «Schedule…» ▸ «Create schedule…» → diálogo «Create test schedule»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DICRETESSCH-FF3E` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DICRETESSCH-FF3E2` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DICRETESSCH-78D2` |  | número | Trials per stage |  |  |
| `DICRETESSCH-5ED9` |  | lista | Apparatus | valores: Open field |  |
| `DICRETESSCH-97DC` |  | lista | Running order | valores: By animal: all trials of an animal, then the next animal ¦ By trial: trial 1 of every animal, then trial 2, … ¦ Randomised: animals in random order within each trial ¦ Latin square: each animal runs in every position equally often |  |
| `DICRETESSCH-A9F0` |  | número | Random seed |  |  |
| `DICRETESSCH-D174` |  | lista | Counterbalance | valores: No counterbalancing ¦ Apparatus (Latin square across each animal's tests) ¦ Test variable (Latin square across each animal's tests) |  |
| `DICRETESSCH-DA97` |  | lista | Variable | valores: novel_object ¦ social_side ¦ condition | deshabilitado al inicio |
| `DICRETESSCH-3E09` |  | texto | Levels |  | deshabilitado al inicio |
| `DICRETESSCH-AE9E` |  | casilla | Skip combinations that already have a test |  |  |
| `DICRETESSCH-09C4` |  | casilla | Skip stages already completed (training criteria) and retired animals |  |  |
| `DICRETESSCH-6D68` |  | botón | OK |  |  |
| `DICRETESSCH-F733` |  | botón | Cancel |  |  |

## Diálogo «Import DeepLabCut track»

Cómo llegar: Test schedule: cinta «Import track» → diálogo «Import DeepLabCut track»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIIMPDEETRA-7DBF` |  | número | Frame rate (fps) |  |  |
| `DIIMPDEETRA-D5AF` |  | tabla/lista | Body centre = mean of | columnas: 1 |  |
| `DIIMPDEETRA-1952` |  | lista | Head | valores: (none) ¦ nose ¦ tail |  |
| `DIIMPDEETRA-C8E5` |  | lista | Tail base | valores: (none) ¦ nose ¦ tail |  |
| `DIIMPDEETRA-545B` |  | número | Min likelihood |  |  |
| `DIIMPDEETRA-BCE9` |  | casilla | CSV covers the whole video: keep only the test period |  |  |
| `DIIMPDEETRA-EA5D` |  | botón | OK |  |  |
| `DIIMPDEETRA-3BD2` |  | botón | Cancel |  |  |

## Diálogo «Import track data»

Cómo llegar: Test schedule: cinta «Import track data» → diálogo «Import track data»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIIMPTRADAT-3C97` |  | texto | (sin etiqueta) |  |  |
| `DIIMPTRADAT-F4C5` |  | botón | Choose file… |  |  |
| `DIIMPTRADAT-66F3` |  | tabla/lista | (sin etiqueta) |  |  |
| `DIIMPTRADAT-AA85` |  | lista | Time column |  |  |
| `DIIMPTRADAT-6523` |  | lista | Centre X column |  |  |
| `DIIMPTRADAT-F328` |  | lista | Centre Y column |  |  |
| `DIIMPTRADAT-29C3` |  | lista | Head X column |  |  |
| `DIIMPTRADAT-7A0E` |  | lista | Head Y column |  |  |
| `DIIMPTRADAT-62B2` |  | lista | Tail X column |  |  |
| `DIIMPTRADAT-4FA2` |  | lista | Tail Y column |  |  |
| `DIIMPTRADAT-05A6` |  | número | Pixels per unit |  |  |
| `DIIMPTRADAT-89EE` |  | número | X offset (px) |  |  |
| `DIIMPTRADAT-866A` |  | número | Y offset (px) |  |  |
| `DIIMPTRADAT-7D18` |  | casilla | Y axis points up (mirror) |  |  |
| `DIIMPTRADAT-E20F` |  | número | Image height (px) |  |  |
| `DIIMPTRADAT-E132` |  | número | Frame rate if no time column |  |  |
| `DIIMPTRADAT-FE7E` |  | botón | Import |  |  |
| `DIIMPTRADAT-1D07` |  | botón | Cancel |  |  |

## Diálogo «Check video»

Cómo llegar: Test schedule: cinta «Check video» → diálogo «Check video»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DICHEVID-8905` |  | tabla/lista | (sin etiqueta) | columnas: Tests ¦ Video ¦ Frames ¦ Duplicate ¦ Black ¦ Missing ¦ Findings |  |
| `DICHEVID-7763` |  | botón | Close |  |  |

## Diálogo «Test variables»

Cómo llegar: Test schedule: cinta «Test variables» → diálogo «Test variables»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DITESVAR-B147` |  | lista | Novel object | valores: Experiment default (Object B) |  |
| `DITESVAR-45D6` |  | lista | Social stimulus side | valores: Experiment default (Left) ¦ Left ¦ Right |  |
| `DITESVAR-FE71` |  | botón | OK |  |  |
| `DITESVAR-F25C` |  | botón | Cancel |  |  |

## Diálogo «Industrial cameras»

Cómo llegar: Run tests: cinta «Add source» ▸ «Industrial cameras…» → diálogo «Industrial cameras»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIINDCAM-D847` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DIINDCAM-C910` |  | botón | Add .cti file… |  |  |
| `DIINDCAM-1A11` |  | botón | Remove |  |  |
| `DIINDCAM-A858` |  | botón | OK |  |  |
| `DIINDCAM-5BF8` |  | botón | Cancel |  |  |

## Diálogo «Panel settings»

Cómo llegar: Run tests: cinta «Panel settings» → diálogo «Panel settings»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIPANSET-42EC` |  | casilla | Show the toolbar of each test (start, pause, stop…) |  |  |
| `DIPANSET-FAE7` |  | casilla | Show the video source next to the title |  |  |
| `DIPANSET-7A87` |  | casilla | Show the time slider |  |  |
| `DIPANSET-CDA9` |  | casilla | Show the zone, distance and number of events under the image |  |  |
| `DIPANSET-A685` |  | casilla | Show the Session log, Video and Zones tabs |  |  |
| `DIPANSET-B966` |  | botón | OK |  |  |
| `DIPANSET-BFF6` |  | botón | Cancel |  |  |

## Diálogo «Recorded video file names»

Cómo llegar: Run tests: botón «File names…» → diálogo «Recorded video file names»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIRECVIDFILN-E319` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DIRECVIDFILN-97A9` |  | botón | OK |  |  |
| `DIRECVIDFILN-829C` |  | botón | Cancel |  |  |
| `DIRECVIDFILN-D864` |  | botón | Restore Defaults |  |  |

## Diálogo «HTML report»

Cómo llegar: Data ▸ Spreadsheet: cinta «HTML report» → diálogo «HTML report»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIHTMREP-B6E3` |  | texto | Compare groups (statistics and graphs) for these measures |  |  |
| `DIHTMREP-D4BA` |  | tabla/lista | (sin etiqueta) | columnas: 1 |  |
| `DIHTMREP-B867` |  | casilla | Include track plots and heat maps for every test |  |  |
| `DIHTMREP-8BA6` |  | lista | Heat map scale | valores: Automatic (each map) ¦ Same scale for all tests ¦ % of time ¦ Relative (max = 1) |  |
| `DIHTMREP-59DC` |  | casilla | Charts of Speed, Freezing |  |  |
| `DIHTMREP-0A26` |  | botón | Create report… |  |  |
| `DIHTMREP-0310` |  | botón | Cancel |  |  |

## Diálogo «Operant chambers»

Cómo llegar: [modo io_only] Protocol ▸ Protocol: botón «Operant chambers…» → diálogo «Operant chambers»

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIOPECHA-1211` |  | lista | Chamber | valores: Med Associates-style chamber ¦ Coulbourn-style chamber ¦ Lafayette-style chamber ¦ Custom chamber |  |
| `DIOPECHA-460D` |  | lista | Wired to | valores: Arduino (mANY-MAZE I/O firmware) ¦ Firmata board (StandardFirmata) ¦ National Instruments DAQ (NI-DAQmx) ¦ LabJack T4 / T7 / T8 (LJM) ¦ Simulated device |  |
| `DIOPECHA-E323` |  | número | Chambers |  |  |
| `DIOPECHA-B568` |  | tabla/lista | (sin etiqueta) | columnas: Name ¦ Kind ¦ Pin ¦ Role |  |
| `DIOPECHA-9BA5` |  | botón | OK |  |  |
| `DIOPECHA-C8B2` |  | botón | Cancel |  |  |

## Mensajes de la app que hay que provocar al menos una vez

Cómo llegar: cada uno sale de la función indicada: deduce qué lo provoca (entrada inválida, archivo que falta, confirmación…) y provócalo

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `MENDELAAPPQU-3D37` |  | mensaje (information) | 'Weigh the animal' — f'Test {test.id} was not armed: animal {animal.id} must be weighed first (the protocol asks for a weight before each test).' | manymaze/gui/confirm_id.py:40 · función weigh_before_test |  |
| `MENDELAAPPQU-A9AF` |  | mensaje (warning) | 'Wrong animal' — f'“{text.strip()}” does not match animal {test.animal_id} of test {test.id}. Check the animal and scan again.' | manymaze/gui/confirm_id.py:72 · función confirm_animal_id |  |
| `MENDELAAPPQU-BE33` |  | mensaje (information) | 'Calibrate' — 'Select the shocker (or laser) channel first.' | manymaze/gui/io_devices_dialog.py:544 · función calibrate_channel |  |
| `MENDELAAPPQU-E499` |  | mensaje (information) | 'Calibrate' — f"'{ch.get('name')}' has no intensity option: add intensity=<the PWM / analogue output that sets its current> to its options." | manymaze/gui/io_devices_dialog.py:548 · función calibrate_channel |  |
| `MENDELAAPPQU-4903` |  | mensaje (warning) | 'I/O devices' — '\n'.join(problems) | manymaze/gui/io_devices_dialog.py:570 · función connect_devices |  |
| `MENDELAAPPQU-4744` |  | mensaje (warning) | 'I/O devices' — '\n'.join(problems[-10:]) | manymaze/gui/io_devices_dialog.py:577 · función connect_devices |  |
| `MENDELAAPPQU-4D85` |  | mensaje (warning) | 'I/O devices' — f"Device names must be unique: {', '.join(sorted(dup))}" | manymaze/gui/io_devices_dialog.py:686 · función accept |  |
| `MENDELAAPPQU-A882` |  | mensaje (warning) | 'I/O devices' — f"{text}\n\nChange the inputs' period_ms (or the baud rate) before keeping this configuration." | manymaze/gui/io_devices_dialog.py:720 · función check_bandwidth |  |
| `MENDELAAPPQU-88F9` |  | mensaje (question) | 'I/O devices' — f'{text}\n\nKeep this configuration?' | manymaze/gui/io_devices_dialog.py:723 · función check_bandwidth |  |
| `MENDELAAPPQU-D3C7` |  | mensaje (warning) | 'Calibrate' — f"{self.device} · {self.ch['intensity']}: {msg}" | manymaze/gui/io_devices_dialog.py:873 · función set_level |  |
| `MENDELAAPPQU-53FB` |  | mensaje (information) | self.windowTitle() — f'Capture at least {MIN_VIEWS} views of the checkerboard and click Calibrate first (or choose another correction).' | manymaze/gui/live_widgets/lens.py:206 · función accept |  |
| `MENDELAAPPQU-DD74` |  | mensaje (question) | APP_NAME — f'Save changes to “{self.project.name}”?' + (f'\n\nTest {tv.test.id} is still being scored: saving stops the observation and the behaviours still running.' if s | manymaze/gui/main_window.py:1038 · función maybe_save |  |
| `MENDELAAPPQU-4070` |  | mensaje (question) | APP_NAME — 'Tests are still running on the Run tests page.\n\nStop them and save the data recorded so far?' | manymaze/gui/main_window.py:1055 · función _stop_live_tests |  |
| `MENDELAAPPQU-F5C8` |  | mensaje (information) | title — 'The protocol is locked: only an administrator can add or change apparatus (File ▸ Users and security).' | manymaze/gui/main_window.py:1280 · función protocol_locked |  |
| `MENDELAAPPQU-6B7B` |  | mensaje (information) | 'Protect experiment' — 'Only an administrator can change the experiment password.' | manymaze/gui/main_window.py:766 · función protect_experiment |  |
| `MENDELAAPPQU-F2AD` |  | mensaje (information) | 'Protect experiment' — 'This experiment was opened read-only.' | manymaze/gui/main_window.py:770 · función protect_experiment |  |
| `MENDELAAPPQU-1719` |  | mensaje (information) | 'Remove user' — f'{name} has a password: only an administrator (or {name}) can remove this user.' | manymaze/gui/main_window.py:806 · función _remove_user_dialog |  |
| `MENDELAAPPQU-1B74` |  | mensaje (warning) | 'Save as' — f'{dest} is open in {explock.describe(other)}: close it there or choose another folder.' | manymaze/gui/main_window.py:1229 · función save_as |  |
| `MENDELAAPPQU-B600` |  | mensaje (question) | APP_NAME — f'{path} already exists. Open it instead?' | manymaze/gui/main_window.py:1071 · función new_project |  |
| `MENDELAAPPQU-FE42` |  | mensaje (question) | 'Save as' — f'{dest} already exists. Replace its experiment file and tracks?' | manymaze/gui/main_window.py:1224 · función save_as |  |
| `MENDELAAPPQU-842E` |  | mensaje (warning) | 'Import from ANY-maze' — msg + '\n\n' + '\n'.join(res['warnings'][:20]) | manymaze/gui/main_window.py:1326 · función import_anymaze_xml |  |
| `MENDELAAPPQU-8A4B` |  | mensaje (information) | 'Restore a backup' — 'This experiment has no backups yet. A backup of the experiment file is made when it is saved (at most every 10 minutes).' | manymaze/gui/main_window.py:1395 · función restore_backup |  |
| `MENDELAAPPQU-A3A4` |  | mensaje (information) | 'Reveal treatment coding' — 'Only an administrator can reveal the treatment coding of this experiment (File ▸ Users and security).' | manymaze/gui/pages/animals.py:517 · función _reveal_toggled |  |
| `MENDELAAPPQU-A37C` |  | mensaje (question) | 'Delete animals' — msg | manymaze/gui/pages/animals.py:648 · función delete_selected_interactive |  |
| `MENDELAAPPQU-363E` |  | mensaje (information) | 'Training criteria' — 'No training criteria are defined. Add them on the Protocol (Training criteria).' | manymaze/gui/pages/animals.py:764 · función criteria_dialog |  |
| `MENDELAAPPQU-04E8` |  | mensaje (information) | 'Randomise treatments' — 'Add at least two treatments first.' | manymaze/gui/pages/animals.py:799 · función randomise_dialog |  |
| `MENDELAAPPQU-C35A` |  | mensaje (warning) | 'Rename treatment' — f'A treatment named “{new}” already exists.' | manymaze/gui/pages/animals.py:893 · función _treatment_changed |  |
| `MENDELAAPPQU-A1E2` |  | mensaje (question) | 'Delete treatment' — msg | manymaze/gui/pages/animals.py:959 · función _delete_group_dialog |  |
| `MENDELAAPPQU-9392` |  | mensaje (warning) | 'Add field' — f'“{name.strip()}” is already a column.' | manymaze/gui/pages/animals.py:977 · función _add_field_dialog |  |
| `MENDELAAPPQU-CBEE` |  | mensaje (warning) | 'Rename field' — f'“{new.strip()}” is already a column.' | manymaze/gui/pages/animals.py:1004 · función _rename_field_dialog |  |
| `MENDELAAPPQU-3EBF` |  | mensaje (question) | 'Remove field' — f'Remove the column “{name}” and its values for every animal?' | manymaze/gui/pages/animals.py:1010 · función _remove_field_dialog |  |
| `MENDELAAPPQU-6B40` |  | mensaje (question) | 'Reveal treatment coding' — 'Reveal the treatments? The experimenter will no longer be blind to the treatment of each animal.' | manymaze/gui/pages/animals.py:520 · función _reveal_toggled |  |
| `MENDELAAPPQU-E5DA` |  | mensaje (warning) | 'Add treatment' — f'A treatment named “{name.strip()}” already exists.' | manymaze/gui/pages/animals.py:932 · función _add_group_dialog |  |
| `MENDELAAPPQU-8D5E` |  | mensaje (warning) | 'Rename treatment' — f'A treatment named “{new.strip()}” already exists.' | manymaze/gui/pages/animals.py:941 · función _rename_group_dialog |  |
| `MENDELAAPPQU-8892` |  | mensaje (warning) | 'Animals' — f'There is already an animal with ID “{text}”. Animal IDs must be unique.' | manymaze/gui/pages/animals.py:678 · función _cell_changed |  |
| `MENDELAAPPQU-6D11` |  | mensaje (warning) | 'Background image' — msg | manymaze/gui/pages/apparatus/background.py:169 · función load |  |
| `MENDELAAPPQU-F118` |  | mensaje (information) | 'Lens correction' — 'No test has a video yet. Lens correction applies to the videos of tests; for a camera, use Camera options on the Run tests page.' | manymaze/gui/pages/apparatus/page.py:496 · función lens_correction |  |
| `MENDELAAPPQU-7D02` |  | mensaje (warning) | 'Rename apparatus' — f'An apparatus called “{new_name}” already exists.' | manymaze/gui/pages/apparatus/page.py:573 · función _rename_apparatus |  |
| `MENDELAAPPQU-815D` |  | mensaje (warning) | 'Import apparatus' — f'Cannot read apparatus from {source}:\n{user_message(e)}' | manymaze/gui/pages/apparatus/page.py:446 · función import_apparatus |  |
| `MENDELAAPPQU-D6B9` |  | mensaje (question) | 'Delete apparatus' — msg | manymaze/gui/pages/apparatus/page.py:599 · función delete_apparatus |  |
| `MENDELAAPPQU-CF62` |  | mensaje (warning) | 'Calibrate' — str(e) | manymaze/gui/pages/apparatus/page.py:868 · función calibrate_from_line |  |
| `MENDELAAPPQU-EB6B` |  | mensaje (warning) | 'Add grid' — str(e) | manymaze/gui/pages/apparatus/page.py:1063 · función add_grid |  |
| `MENDELAAPPQU-55DE` |  | mensaje (question) | 'Operant chambers' — f'The experiment already has I/O devices ({names}). Replace them with the chambers?\n\nNo adds the chambers to them.' | manymaze/gui/pages/experiment.py:816 · función set_up_chambers |  |
| `MENDELAAPPQU-AFC8` |  | mensaje (information) | 'Unblind' — 'Only an administrator can reveal the treatment coding of this experiment (File ▸ Users and security).' | manymaze/gui/pages/experiment.py:1162 · función _blind_toggled |  |
| `MENDELAAPPQU-3446` |  | mensaje (information) | 'Insert measure' — "The measures are listed once the experiment has tests with results. You can also type a measure's name in braces, e.g. {Total distance (m)}." | manymaze/gui/pages/experiment.py:1006 · función _pick_measure |  |
| `MENDELAAPPQU-E530` |  | mensaje (question) | 'Apply template' — f'Make this a {t.title} protocol with tests of {t.default_duration_s:g} s? Existing results are recalculated with the new protocol type.' | manymaze/gui/pages/experiment.py:1042 · función apply_template |  |
| `MENDELAAPPQU-2460` |  | mensaje (question) | 'Restore defaults' — f'Restore the default {what} settings?' | manymaze/gui/pages/experiment.py:1078 · función _restore_defaults |  |
| `MENDELAAPPQU-D64D` |  | mensaje (question) | 'Unblind' — 'Reveal the treatment groups? The experimenter will no longer be blind to the treatments on the Experiment, Test schedule, Run tests and Review and score pages. | manymaze/gui/pages/experiment.py:1165 · función _blind_toggled |  |
| `MENDELAAPPQU-FB19` |  | mensaje (question) | 'Delete calculation' — msg | manymaze/gui/pages/experiment_calcs.py:126 · función delete_calculation |  |
| `MENDELAAPPQU-1776` |  | mensaje (warning) | 'Analysis plug-ins' — msg + '\n\n' + '\n'.join(lines) | manymaze/gui/pages/experiment_calcs.py:217 · función run_plugins |  |
| `MENDELAAPPQU-57BD` |  | mensaje (warning) | 'Analysis plug-ins' — m | manymaze/gui/pages/experiment_calcs.py:222 · función run_plugins |  |
| `MENDELAAPPQU-D3BD` |  | mensaje (question) | 'Delete key' — f"The key “{b.name}” has {n} scored event{('s' if n != 1 else '')} in {n_tests} test{('s' if n_tests != 1 else '')}. Delete the key?\n\nThe events stay in the t | manymaze/gui/pages/experiment_keys.py:93 · función _remove_behaviour |  |
| `MENDELAAPPQU-C91C` |  | mensaje (question) | 'Stop all tests' — 'Stop every test now?\n\nSave keeps the data recorded so far; Discard throws the tests away.' | manymaze/gui/pages/live/multi.py:856 · función _stop_all_clicked |  |
| `MENDELAAPPQU-1E20` |  | mensaje (information) | 'Run tests' — 'Stop this test before removing it.' | manymaze/gui/pages/live/multi.py:110 · función remove_session_row |  |
| `MENDELAAPPQU-ADA5` |  | mensaje (information) | 'Run tests' — 'Add a camera or a video file first (Add camera / video source).' | manymaze/gui/pages/live/multi.py:443 · función start_cameras |  |
| `MENDELAAPPQU-5852` |  | mensaje (information) | 'Camera options' — 'Add a camera or a video file first.' | manymaze/gui/pages/live/multi.py:469 · función multi_camera_options |  |
| `MENDELAAPPQU-2452` |  | mensaje (information) | 'Camera options' — 'Stop the tests using this camera first.' | manymaze/gui/pages/live/multi.py:473 · función multi_camera_options |  |
| `MENDELAAPPQU-ADA52` |  | mensaje (information) | 'Run tests' — 'Add a camera or a video file first (Add camera / video source).' | manymaze/gui/pages/live/multi.py:79 · función add_session_row |  |
| `MENDELAAPPQU-5446` |  | mensaje (information) | 'Run tests' — 'Stop the tests before stopping the cameras.' | manymaze/gui/pages/live/multi.py:435 · función _toggle_cameras |  |
| `MENDELAAPPQU-D7BE` |  | mensaje (information) | 'Run tests' — 'Save the experiment first.' | manymaze/gui/pages/live/multi.py:584 · función arm_row |  |
| `MENDELAAPPQU-C5A0` |  | mensaje (warning) | 'Run tests' — f'{e.label}: {msg}' | manymaze/gui/pages/live/multi.py:599 · función arm_row |  |
| `MENDELAAPPQU-DC7B` |  | mensaje (question) | 'Run tests' — f"This experiment's procedures run these programs on this computer:\n\n{listing}\n\nAllow them? Only allow programs you trust: a project file can come from anyo | manymaze/gui/pages/live/page.py:742 · función procedures_ready |  |
| `MENDELAAPPQU-CD08` |  | mensaje (information) | 'Run tests' — 'Stop the running test(s) before changing the mode.' | manymaze/gui/pages/live/page.py:760 · función set_mode |  |
| `MENDELAAPPQU-070C` |  | mensaje (critical) | 'Run tests' — msg | manymaze/gui/pages/live/page.py:728 · función procedures_ready |  |
| `MENDELAAPPQU-14E3` |  | mensaje (question) | 'Stop test' — 'Stop the test now?\n\nSave keeps the data recorded so far; Discard throws the test away.' | manymaze/gui/pages/live/single.py:871 · función _stop_clicked |  |
| `MENDELAAPPQU-B8D0` |  | mensaje (information) | 'Camera options' — 'Choose a camera or a video file first.' | manymaze/gui/pages/live/single.py:230 · función camera_options |  |
| `MENDELAAPPQU-9316` |  | mensaje (information) | 'Run tests' — 'Choose a video file to simulate a camera first.' | manymaze/gui/pages/live/single.py:287 · función start_preview |  |
| `MENDELAAPPQU-C193` |  | mensaje (information) | 'Background' — 'Start the preview first, with the arena empty.' | manymaze/gui/pages/live/single.py:589 · función capture_background |  |
| `MENDELAAPPQU-D7BE2` |  | mensaje (information) | 'Run tests' — 'Save the experiment first.' | manymaze/gui/pages/live/single.py:605 · función _prepare_test |  |
| `MENDELAAPPQU-23B4` |  | mensaje (information) | 'Run tests' — 'Draw an apparatus first (Apparatus page).' | manymaze/gui/pages/live/single.py:608 · función _prepare_test |  |
| `MENDELAAPPQU-94E6` |  | mensaje (information) | 'Run tests' — 'Choose or type the animal ID.' | manymaze/gui/pages/live/single.py:612 · función _prepare_test |  |
| `MENDELAAPPQU-57B6` |  | mensaje (information) | 'Run tests' — 'Stop the test before stopping the camera.' | manymaze/gui/pages/live/single.py:331 · función _toggle_preview |  |
| `MENDELAAPPQU-10D3` |  | mensaje (information) | 'E-mail report' — 'No e-mail server is set up. Add an Alerts (e-mail / SMS) device with its SMTP server in Protocol ▸ Hardware ▸ I/O devices: reports are sent through it, as the  | manymaze/gui/pages/results/exports.py:345 · función email_report |  |
| `MENDELAAPPQU-8939` |  | mensaje (question) | 'Save report as' — f'Replace the report “{name}”?' | manymaze/gui/pages/results/page.py:860 · función save_report_as |  |
| `MENDELAAPPQU-FFF4` |  | mensaje (question) | 'Delete report' — f"Delete the report “{rep['name']}”? (The spreadsheet keeps showing what it shows.)" | manymaze/gui/pages/results/page.py:877 · función delete_report |  |
| `MENDELAAPPQU-3B46` |  | mensaje (information) | title — why | manymaze/gui/pages/tests.py:691 · función _refuse_busy |  |
| `MENDELAAPPQU-2B31` |  | mensaje (information) | 'Join video files' — 'Choose at least two video files.' | manymaze/gui/pages/tests.py:810 · función join_videos |  |
| `MENDELAAPPQU-0843` |  | mensaje (information) | 'Create schedule' — 'Add animals first (Animals page).' | manymaze/gui/pages/tests.py:872 · función create_schedule |  |
| `MENDELAAPPQU-0FEA` |  | mensaje (information) | 'Track' — 'No tests to track (tests need an existing video and must not be excluded).' | manymaze/gui/pages/tests.py:1077 · función track_tests |  |
| `MENDELAAPPQU-A305` |  | mensaje (information) | 'Check video' — 'The selected tests have no video.' | manymaze/gui/pages/tests.py:1144 · función check_videos |  |
| `MENDELAAPPQU-239B` |  | mensaje (question) | 'Delete tests' — f"Delete {self.describe_tests(tests)} and {('their' if len(tests) != 1 else 'its')} tracks? Video files are not deleted." | manymaze/gui/pages/tests.py:913 · función delete_selected |  |
| `MENDELAAPPQU-D4F2` |  | mensaje (question) | 'End stage for animal' — f'End the stage for {text}?\n\nTheir remaining tests of the stage are skipped and new schedules leave the stage out. Use “Reopen stage for animal” to undo.' | manymaze/gui/pages/tests.py:968 · función end_stage_selected |  |
| `MENDELAAPPQU-ACD5` |  | mensaje (question) | 'Clear tracks' — f"Delete the tracks of {len(tests)} test{('s' if len(tests) != 1 else '')}? Videos and scored events are kept." | manymaze/gui/pages/tests.py:1029 · función clear_selected_tracks |  |
| `MENDELAAPPQU-9F72` |  | mensaje (question) | 'Apply to all tests' — f"Use this test's detection settings for all {len(others)} other tests? (Their own per-test settings are replaced.)" | manymaze/gui/pages/testview/detection.py:211 · función apply_detection_to_all |  |
| `MENDELAAPPQU-C8A5` |  | mensaje (question) | 'Clear events' — f'Delete all {len(self.test.events)} scored events of test {self.test.id}?' | manymaze/gui/pages/testview/scoring.py:436 · función clear_events |  |
| `MENDELAAPPQU-8D6A` |  | mensaje (question) | 'Observation' — f'Test {t.id} already has {len(t.events)} scored events. Delete them and score the test again?' | manymaze/gui/pages/testview/scoring.py:329 · función clock_start |  |
| `MENDELAAPPQU-A5E1` |  | mensaje (question) | 'Remove pose model' — f"Delete the installed {pose.MODELS[m]['title']}?" | manymaze/gui/pose_model.py:177 · función remove |  |
| `MENDELAAPPQU-DF31` |  | mensaje (warning) | 'Invalid JSON' — str(e) | manymaze/gui/procedure_editor.py:987 · función edit_json |  |
| `MENDELAAPPQU-E268` |  | mensaje (question) | 'Clear password' — f'{name} is the only administrator: without the password nobody is one any more and the security settings stop applying. Clear it?' | manymaze/gui/security_dialog.py:221 · función clear_password |  |
| `MENDELAAPPQU-21B7` |  | mensaje (warning) | 'Users and security' — str(e) | manymaze/gui/security_dialog.py:237 · función toggle_role |  |
| `MENDELAAPPQU-DEA7` |  | mensaje (warning) | 'Check for updates' — f'Could not check for updates: {msg}' | manymaze/gui/updates.py:53 · función check_updates |  |
| `MENDELAAPPQU-4F10` |  | mensaje (information) | 'Check for updates' — info.message() | manymaze/gui/updates.py:77 · función show_update_result |  |
| `MENDELAAPPQU-3495` |  | mensaje (warning) | 'Check for updates' — info.message() | manymaze/gui/updates.py:79 · función show_update_result |  |
| `MENDELAAPPQU-7F76` |  | mensaje (critical) | title — exc | manymaze/gui/widgets.py:68 · función error_box |  |

## Procedimientos: eventos (cada uno en un «when»)

Cómo llegar: Protocol ▸ Procedures → Add ▾ → when

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROEVECADUNO-213B` | Test | evento | Test is waiting to start | parámetros:  | test_waiting |
| `PROEVECADUNO-9353` | Test | evento | Test starts | parámetros:  | test_start |
| `PROEVECADUNO-5929` | Test | evento | Test ends | parámetros:  | test_end |
| `PROEVECADUNO-8A87` | Test | evento | Time reached | parámetros: Time (s) | time_reached |
| `PROEVECADUNO-2966` | Test | evento | Every N seconds | parámetros: Interval (s), First at (s) | every |
| `PROEVECADUNO-B07C` | Test | evento | Test paused | parámetros:  | test_paused |
| `PROEVECADUNO-0F89` | Test | evento | Test resumed | parámetros:  | test_resumed |
| `PROEVECADUNO-2868` | Zones | evento | Animal enters zone | parámetros: Zone | zone_enter |
| `PROEVECADUNO-BBC2` | Zones | evento | Animal leaves zone | parámetros: Zone | zone_exit |
| `PROEVECADUNO-84DB` | Zones | evento | Head enters zone | parámetros: Zone | head_zone_enter |
| `PROEVECADUNO-9A22` | Zones | evento | Head leaves zone | parámetros: Zone | head_zone_exit |
| `PROEVECADUNO-E055` | Zones | evento | Total time in zone reaches | parámetros: Zone, Time (s) | zone_time_reaches |
| `PROEVECADUNO-6459` | Zones | evento | Time in zone (one visit) reaches | parámetros: Zone, Time (s) | zone_dwell |
| `PROEVECADUNO-9B92` | Zones | evento | Zone entries reach | parámetros: Zone, Entries | zone_entries_reach |
| `PROEVECADUNO-FAEC` | Zones | evento | Zone sequence completed | parámetros: Zones (in order) | zone_sequence |
| `PROEVECADUNO-3881` | Animal | evento | Freezing starts | parámetros:  | freezing_start |
| `PROEVECADUNO-F5C2` | Animal | evento | Freezing ends | parámetros:  | freezing_end |
| `PROEVECADUNO-C586` | Animal | evento | Immobility starts | parámetros:  | immobile_start |
| `PROEVECADUNO-0E1A` | Animal | evento | Immobility ends | parámetros:  | immobile_end |
| `PROEVECADUNO-2828` | Animal | evento | Animal not detected | parámetros:  | animal_lost |
| `PROEVECADUNO-DF37` | Animal | evento | Animal detected again | parámetros:  | animal_found |
| `PROEVECADUNO-A57B` | Animal | evento | Speed rises above | parámetros: Speed | speed_above |
| `PROEVECADUNO-BC96` | Animal | evento | Speed falls below | parámetros: Speed | speed_below |
| `PROEVECADUNO-FB65` | Animal | evento | Distance travelled reaches | parámetros: Distance | distance_reaches |
| `PROEVECADUNO-9539` | Animal | evento | Total freezing time reaches | parámetros: Time (s) | freezing_time_reaches |
| `PROEVECADUNO-1A82` | Animal | evento | Total immobile time reaches | parámetros: Time (s) | immobile_time_reaches |
| `PROEVECADUNO-5B84` | Keyboard | evento | Key pressed | parámetros: Key | key_down |
| `PROEVECADUNO-A1A5` | Keyboard | evento | Key released | parámetros: Key | key_up |
| `PROEVECADUNO-4487` | Inputs | evento | Input switches on | parámetros: Device, Input | input_on |
| `PROEVECADUNO-FF9F` | Inputs | evento | Input switches off | parámetros: Device, Input | input_off |
| `PROEVECADUNO-F7D6` | Inputs | evento | Input changes | parámetros: Device, Input | input_changed |
| `PROEVECADUNO-7710` | Inputs | evento | Input activations reach | parámetros: Device, Input, Count | input_count_reaches |
| `PROEVECADUNO-AEC3` | Inputs | evento | Analogue input rises above | parámetros: Device, Input, Level | analog_above |
| `PROEVECADUNO-B975` | Inputs | evento | Analogue input falls below | parámetros: Device, Input, Level | analog_below |
| `PROEVECADUNO-7B51` | Inputs | evento | Encoder count reaches | parámetros: Device, Input, Counts | encoder_reaches |
| `PROEVECADUNO-E769` | Inputs | evento | Every N encoder counts | parámetros: Device, Input, Counts | encoder_every |
| `PROEVECADUNO-4984` | Inputs | evento | Movement detector: movement starts | parámetros: Device, Detector | movement_start |
| `PROEVECADUNO-3377` | Inputs | evento | Movement detector: movement ends | parámetros: Device, Detector | movement_end |
| `PROEVECADUNO-4301` | Sensors | evento | Sensor rises above | parámetros: Device, Sensor, Level | sensor_above |
| `PROEVECADUNO-E846` | Sensors | evento | Sensor falls below | parámetros: Device, Sensor, Level | sensor_below |
| `PROEVECADUNO-1D56` | Sensors | evento | Sensor leaves its alert range | parámetros: Device, Sensor | sensor_out_of_range |
| `PROEVECADUNO-698F` | Sensors | evento | Sensor back in its alert range | parámetros: Device, Sensor | sensor_in_range |
| `PROEVECADUNO-7A86` | Sensors | evento | Temperature controller reaches its target | parámetros: Device, Temperature controller | temperature_reached |
| `PROEVECADUNO-E899` | Pumps | evento | Pump reaches its target volume | parámetros: Device, Pump | pump_target_reached |
| `PROEVECADUNO-3C31` | Pumps | evento | Pump stalled | parámetros: Device, Pump | pump_stalled |
| `PROEVECADUNO-37AB` | Pumps | evento | Volume infused reaches | parámetros: Device, Pump, Volume (ml) | pump_volume_reaches |
| `PROEVECADUNO-988D` | Outputs | evento | Output switched on | parámetros: Device, Output | output_on |
| `PROEVECADUNO-38812` | Outputs | evento | Output switched off | parámetros: Device, Output | output_off |
| `PROEVECADUNO-BF95` | Outputs | evento | Light ramp finished | parámetros: Device, Light | light_ramp_done |
| `PROEVECADUNO-38F3` | Outputs | evento | Pulse sequence finished | parámetros: Device, Output | pulse_sequence_done |
| `PROEVECADUNO-172B` | Outputs | evento | Pellet detected | parámetros: Device, Dispenser | pellet_dropped |
| `PROEVECADUNO-E5A3` | Outputs | evento | Pellet dispenser error | parámetros: Device, Dispenser | pellet_error |
| `PROEVECADUNO-2482` | Logic | evento | Variable changes | parámetros: Variable | variable_changed |
| `PROEVECADUNO-184B` | Logic | evento | Condition becomes true | parámetros: Condition | condition_true |
| `PROEVECADUNO-FB652` | Logic | evento | Condition becomes false | parámetros: Condition | condition_false |
| `PROEVECADUNO-9A98` | Logic | evento | Timer elapses | parámetros: Timer | timer_elapsed |
| `PROEVECADUNO-31EC` | Logic | evento | Signal received | parámetros: Signal | signal |
| `PROEVECADUNO-DB5B` | Logic | evento | Virtual switch on | parámetros: Switch | virtual_switch_on |
| `PROEVECADUNO-1998` | Logic | evento | Virtual switch off | parámetros: Switch | virtual_switch_off |
| `PROEVECADUNO-08C8` | Logic | evento | Event marked | parámetros: Event | event_marked |
| `PROEVECADUNO-7C0B` | Logic | evento | Reinforcer earned | parámetros: Schedule | reinforcer_earned |
| `PROEVECADUNO-6C1F` | Touch screen | evento | Touch in area | parámetros: Area | touch |
| `PROEVECADUNO-AE3F` | Touch screen | evento | Touch outside all areas | parámetros:  | touch_outside |
| `PROEVECADUNO-35F0` | Test | evento | Test continued | parámetros:  | test_continuation |
| `PROEVECADUNO-C6AC` | Test | evento | Time until the test ends | parámetros: Time before the end (s) | time_before_end |
| `PROEVECADUNO-03BD` | Test | evento | At random intervals | parámetros: Shortest interval (s), Longest interval (s) | random_interval |
| `PROEVECADUNO-C251` | Test | evento | Time of day reached | parámetros: Time of day (HH:MM[:SS]) | time_of_day |
| `PROEVECADUNO-5FF7` | Zones | evento | Investigation of a zone starts | parámetros: Zone | investigation_start |
| `PROEVECADUNO-A947` | Zones | evento | Investigation of a zone stops | parámetros: Zone | investigation_end |
| `PROEVECADUNO-9A1C` | Zones | evento | Animal turns towards a zone or point | parámetros: Zone or point, Within (°) | oriented_towards |
| `PROEVECADUNO-4886` | Zones | evento | Animal turns away from a zone or point | parámetros: Zone or point, Within (°) | oriented_away |
| `PROEVECADUNO-A9A7` | Zones | evento | Partial exit from a hidden zone | parámetros: Zone | hidden_partial_exit |
| `PROEVECADUNO-63A0` | Zones | evento | Animal fails to enter a zone for a time | parámetros: Zone, Time (s) | zone_not_entered |
| `PROEVECADUNO-43D7` | Animal | evento | Position changes | parámetros: Body part, By at least (px) | position_changed |
| `PROEVECADUNO-5342` | Animal | evento | Rearing starts | parámetros:  | rearing_start |
| `PROEVECADUNO-9E26` | Animal | evento | Rearing stops | parámetros:  | rearing_end |
| `PROEVECADUNO-BB78` | Inputs | evento | Encoder starts turning clockwise | parámetros: Device, Encoder | encoder_cw |
| `PROEVECADUNO-9F5A` | Inputs | evento | Encoder starts turning anticlockwise | parámetros: Device, Encoder | encoder_ccw |
| `PROEVECADUNO-A794` | Inputs | evento | Encoder changes direction | parámetros: Device, Encoder | encoder_reversed |
| `PROEVECADUNO-1ABB` | Inputs | evento | Encoder speed rises above | parámetros: Device, Encoder, RPM | encoder_rpm_above |
| `PROEVECADUNO-AFE3` | Inputs | evento | Encoder speed falls below | parámetros: Device, Encoder, RPM | encoder_rpm_below |
| `PROEVECADUNO-3516` | Audio | evento | Speaker starts | parámetros: Audio device | speaker_start |
| `PROEVECADUNO-2A7B` | Audio | evento | Speaker stops | parámetros: Audio device | speaker_stop |
| `PROEVECADUNO-9DAF` | Audio | evento | Sound file finished | parámetros: Audio device | sound_file_end |
| `PROEVECADUNO-8D5F` | Outputs | evento | Analogue output changes | parámetros: Device, Output | analog_output_changed |
| `PROEVECADUNO-9A38` | Logic | evento | Condition true for a time | parámetros: Condition, For (s) | condition_held |
| `PROEVECADUNO-55C0` | Logic | evento | Value changes within a time | parámetros: Value, By at least, Within (s) | value_changes_by |
| `PROEVECADUNO-CC0E` | System | evento | Disk space low | parámetros:  | disk_space_low |
| `PROEVECADUNO-FAE0` | System | evento | Disk full | parámetros:  | disk_full |
| `PROEVECADUNO-339F` | System | evento | Recording error | parámetros:  | recording_error |

## Procedimientos: acciones (cada una en un «do»)

Cómo llegar: Protocol ▸ Procedures → Add ▾ → do

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROACCCADUNA-18E2` | Outputs | acción | Switch output on | parámetros: Device, Output | output_on |
| `PROACCCADUNA-22D6` | Outputs | acción | Switch output off | parámetros: Device, Output | output_off |
| `PROACCCADUNA-B75B` | Outputs | acción | Toggle output | parámetros: Device, Output | output_toggle |
| `PROACCCADUNA-28F8` | Outputs | acción | Pulse output | parámetros: Device, Output, Duration (s) | output_pulse |
| `PROACCCADUNA-E274` | Outputs | acción | Set output level | parámetros: Device, Output, Level (0–1) | output_set |
| `PROACCCADUNA-DB13` | Outputs | acción | Set analogue output (V) | parámetros: Device, Output, Level (V) | output_volts |
| `PROACCCADUNA-7B2F` | Outputs | acción | Switch all outputs off | parámetros: Device | all_outputs_off |
| `PROACCCADUNA-77AE` | Outputs | acción | Pulse train (optogenetics) | parámetros: Device, Output, Frequency (Hz), Pulse width (ms), Duration (s) | pulse_train |
| `PROACCCADUNA-E28A` | Outputs | acción | Stop pulse train | parámetros: Device, Output | pulse_train_stop |
| `PROACCCADUNA-E5B1` | Outputs | acción | Sync pulse (e-phys / imaging) | parámetros: Device, Output, Width (ms) | sync_pulse |
| `PROACCCADUNA-3BA4` | Operant | acción | Dispense pellet(s) | parámetros: Device, Output, Pellets, Pulse (ms), Gap between pellets (s), Pellet sensor, Detection time (s), Retries | pellet |
| `PROACCCADUNA-24D7` | Operant | acción | Present liquid dipper | parámetros: Device, Output, Duration (s) | dipper |
| `PROACCCADUNA-8740` | Operant | acción | Deliver liquid drops (dripper) | parámetros: Device, Output, Drops, Valve open (ms), Gap between drops (s) | liquid_drop |
| `PROACCCADUNA-1238` | Operant | acción | Present odour | parámetros: Device, Olfactometer, Odour, Air flow (l/min) | odour |
| `PROACCCADUNA-9BFB` | Operant | acción | Stop odour | parámetros: Device, Olfactometer | odour_off |
| `PROACCCADUNA-5D7B` | Lights | acción | Set light level | parámetros: Device, Output, Level (%) | light_level |
| `PROACCCADUNA-6692` | Lights | acción | Ramp light level | parámetros: Device, Output, To level (%), Over (s), From level (%) | light_ramp |
| `PROACCCADUNA-E4F8` | Operant | acción | Light on | parámetros: Device, Output | light_on |
| `PROACCCADUNA-68FE` | Operant | acción | Light off | parámetros: Device, Output | light_off |
| `PROACCCADUNA-B8DF` | Operant | acción | Open door | parámetros: Device, Output | door_open |
| `PROACCCADUNA-4D6A` | Operant | acción | Close door | parámetros: Device, Output | door_close |
| `PROACCCADUNA-A74F` | Operant | acción | Extend lever | parámetros: Device, Output | lever_extend |
| `PROACCCADUNA-520B` | Operant | acción | Retract lever | parámetros: Device, Output | lever_retract |
| `PROACCCADUNA-FCC5` | Operant | acción | Start reinforcement schedule | parámetros: Schedule name, Schedule | schedule_start |
| `PROACCCADUNA-CFA6` | Operant | acción | Register response | parámetros: Schedule name, Schedule, Store 1/0 (reinforced) in | schedule_response |
| `PROACCCADUNA-EDD5` | Shock | acción | Shock on | parámetros: Device, Output, Safety cut-off (s), Intensity (mA) | shock_on |
| `PROACCCADUNA-869C` | Shock | acción | Shock off | parámetros: Device, Output | shock_off |
| `PROACCCADUNA-304D` | Shock | acción | Shock for a duration | parámetros: Device, Output, Duration (s), Intensity (mA) | shock_pulse |
| `PROACCCADUNA-FEAD` | Shock | acción | Set shock intensity | parámetros: Device, Output, Intensity (mA) | shock_intensity |
| `PROACCCADUNA-F108` | Optogenetics | acción | Laser pulse train (duty cycle) | parámetros: Device, Output, Frequency (Hz), Duty cycle (%), Duration (s), Intensity (%) | opto_train |
| `PROACCCADUNA-8132` | Optogenetics | acción | Laser pulse sequence from a file | parámetros: Device, Output, File (CSV), Repeat, Intensity (%) | opto_sequence |
| `PROACCCADUNA-B84D` | Optogenetics | acción | Set laser intensity | parámetros: Device, Output, Intensity (%) | opto_intensity |
| `PROACCCADUNA-52B8` | Temperature | acción | Set temperature | parámetros: Device, Temperature controller, Target (°C), Ramp (°C/min) | set_temperature |
| `PROACCCADUNA-E66D` | Temperature | acción | Temperature control off | parámetros: Device, Temperature controller | temperature_off |
| `PROACCCADUNA-5156` | Pumps | acción | Infuse | parámetros: Device, Pump, Rate (ml/min), Volume (ml) | pump_infuse |
| `PROACCCADUNA-F45C` | Pumps | acción | Withdraw | parámetros: Device, Pump, Rate (ml/min), Volume (ml) | pump_withdraw |
| `PROACCCADUNA-ECA1` | Pumps | acción | Stop pump | parámetros: Device, Pump | pump_stop |
| `PROACCCADUNA-1BE4` | Pumps | acción | Select syringe | parámetros: Device, Pump, Syringe | pump_syringe |
| `PROACCCADUNA-0C81` | Sensors | acción | Tare sensor (zero) | parámetros: Device, Sensor | tare_sensor |
| `PROACCCADUNA-4F26` | Sensors | acción | Store sensor reading | parámetros: Device, Sensor, Store in | read_sensor |
| `PROACCCADUNA-3F37` | Sensors | acción | Weigh the animal (balance) | parámetros: Balance, Store in | weigh_animal |
| `PROACCCADUNA-D278` | Communication | acción | Send alert (e-mail / SMS) | parámetros: Message | send_alert |
| `PROACCCADUNA-ED71` | Audio | acción | Play tone | parámetros: Audio device, Frequency (Hz), Duration (s), Volume (0–1) | tone |
| `PROACCCADUNA-5912` | Audio | acción | Play white noise | parámetros: Audio device, Duration (s), Volume (0–1) | white_noise |
| `PROACCCADUNA-2FFA` | Audio | acción | Play sound file | parámetros: Audio device, File (WAV), Duration (s), Volume (0–1), Play | play_sound |
| `PROACCCADUNA-E1B4` | Audio | acción | Play sound file repeatedly | parámetros: Audio device, File (WAV), Volume (0–1) | loop_sound |
| `PROACCCADUNA-240B` | Audio | acción | Stop sounds | parámetros: Audio device | stop_sound |
| `PROACCCADUNA-F036` | Audio | acción | Beep | parámetros:  | beep |
| `PROACCCADUNA-7D2C` | Communication | acción | Send serial command | parámetros: Device, Command | serial_send |
| `PROACCCADUNA-BC29` | Communication | acción | Send signal | parámetros: Signal | signal |
| `PROACCCADUNA-51562` | Communication | acción | Virtual switch on | parámetros: Switch | virtual_switch_on |
| `PROACCCADUNA-14FB` | Communication | acción | Virtual switch off | parámetros: Switch | virtual_switch_off |
| `PROACCCADUNA-858E` | Communication | acción | Toggle virtual switch | parámetros: Switch | virtual_switch_toggle |
| `PROACCCADUNA-6004` | Communication | acción | Simulate input | parámetros: Device, Input, Value | simulate_input |
| `PROACCCADUNA-A602` | Variables | acción | Set variable | parámetros: Variable, Value | set_variable |
| `PROACCCADUNA-D558` | Variables | acción | Increment variable | parámetros: Variable, By | increment |
| `PROACCCADUNA-B582` | Variables | acción | Decrement variable | parámetros: Variable, By | decrement |
| `PROACCCADUNA-680C` | Variables | acción | Append to array | parámetros: Array, Value | array_append |
| `PROACCCADUNA-1386` | Variables | acción | Start timer | parámetros: Timer, Elapses after (s) | start_timer |
| `PROACCCADUNA-A561` | Variables | acción | Stop timer | parámetros: Timer | stop_timer |
| `PROACCCADUNA-F8FD` | Variables | acción | Reset timer | parámetros: Timer | reset_timer |
| `PROACCCADUNA-8EA3` | Test | acción | Mark event | parámetros: Event | mark |
| `PROACCCADUNA-DB9B` | Test | acción | Start state event | parámetros: Event | mark_start |
| `PROACCCADUNA-90B0` | Test | acción | End state event | parámetros: Event | mark_end |
| `PROACCCADUNA-AB33` | Test | acción | Write to log | parámetros: Message | log |
| `PROACCCADUNA-1A90` | Test | acción | End the test | parámetros: Reason, Allow the test to be continued | end_test |
| `PROACCCADUNA-3C7B` | Test | acción | Pause the test | parámetros:  | pause_test |
| `PROACCCADUNA-754C` | Test | acción | Resume the test | parámetros:  | resume_test |
| `PROACCCADUNA-C504` | Test | acción | Prevent test start | parámetros:  | prevent_test_start |
| `PROACCCADUNA-69EC` | Test | acción | Allow test start | parámetros:  | allow_test_start |
| `PROACCCADUNA-4517` | Test | acción | Run sub-procedure | parámetros: Sub-procedure | run_subprocedure |
| `PROACCCADUNA-FF56` | Test | acción | Enable procedure | parámetros: Procedure | enable_procedure |
| `PROACCCADUNA-7AC0` | Test | acción | Disable procedure | parámetros: Procedure | disable_procedure |
| `PROACCCADUNA-23AD` | Touch screen | acción | Show stimulus | parámetros: Area, Image, Shape, Colour | show_stimulus |
| `PROACCCADUNA-3FAB` | Touch screen | acción | Hide stimulus | parámetros: Area | hide_stimulus |
| `PROACCCADUNA-773C` | Touch screen | acción | Clear screen | parámetros:  | clear_screen |
| `PROACCCADUNA-8FE4` | Test | acción | Schedule another test for this animal | parámetros: Stage, Apparatus, After (minutes) | schedule_test |
| `PROACCCADUNA-9C59` | Test | acción | Generate a warning | parámetros: Message | warning |
| `PROACCCADUNA-5236` | Test | acción | Generate an error | parámetros: Message, End the test | error |
| `PROACCCADUNA-DD1D` | Zones | acción | Set zone label | parámetros: Zone, Label | set_zone_label |
| `PROACCCADUNA-601A` | Zones | acción | Remove zone label | parámetros: Zone | remove_zone_label |
| `PROACCCADUNA-2097` | Zones | acción | Set moveable zone location | parámetros: Zone or point, Centre x (px), Centre y (px) | move_zone |
| `PROACCCADUNA-3272` | Video | acción | Start video recording | parámetros:  | video_start |
| `PROACCCADUNA-B47C` | Video | acción | Stop video recording | parámetros:  | video_stop |
| `PROACCCADUNA-C7CB` | Video | acción | Pause video recording | parámetros:  | video_pause |
| `PROACCCADUNA-7C0F` | Video | acción | Resume video recording | parámetros:  | video_unpause |
| `PROACCCADUNA-6A22` | Video | acción | Label the video recording | parámetros: Label, Also show it on the video for (s) | video_label |
| `PROACCCADUNA-D33B` | Communication | acción | Show a pop-up message | parámetros: Message, Title | popup |
| `PROACCCADUNA-E320` | Communication | acción | Output text on the display | parámetros: Text name, Text, x (px), y (px), Colour | display_text |
| `PROACCCADUNA-87EA` | Communication | acción | Remove text from the display | parámetros: Text name | display_remove |
| `PROACCCADUNA-2158` | Communication | acción | Clear the text on the display | parámetros:  | display_clear |
| `PROACCCADUNA-B7C9` | Communication | acción | Send an e-mail | parámetros: Message, Subject, To | send_email |
| `PROACCCADUNA-E32C` | Communication | acción | Send an SMS | parámetros: Message, To | send_sms |
| `PROACCCADUNA-1587` | Communication | acción | Run a program | parámetros: Program, Arguments | run_program |
| `PROACCCADUNA-A10E` | Communication | acción | Trigger a plug-in | parámetros: Plug-in, Argument, Store the result in | plugin |
| `PROACCCADUNA-F7B9` | Outputs | acción | Set output frequency | parámetros: Device, Output, Frequency (Hz) | set_output_frequency |
| `PROACCCADUNA-750C` | Outputs | acción | Set output duty cycle | parámetros: Device, Output, Duty cycle (%) | set_output_duty |
| `PROACCCADUNA-40F5` | Outputs | acción | Set output on-duration | parámetros: Device, Output, Duration (s) | set_output_duration |
| `PROACCCADUNA-6043` | Audio | acción | Set speaker volume | parámetros: Audio device, Volume (0–1) | set_volume |

## Procedimientos: funciones de las expresiones

Cómo llegar: Procedures → Functions…

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `PROFUNDELASE-1BEB` |  | función | abs | absolute value | 1–1 argumentos |
| `PROFUNDELASE-180F` |  | función | min | smallest of the values or of a list | 1–99 argumentos |
| `PROFUNDELASE-FB9D` |  | función | max | largest of the values or of a list | 1–99 argumentos |
| `PROFUNDELASE-142D` |  | función | round | round(x, decimals) | 1–2 argumentos |
| `PROFUNDELASE-9E0A` |  | función | floor |  | 1–1 argumentos |
| `PROFUNDELASE-1A18` |  | función | ceil |  | 1–1 argumentos |
| `PROFUNDELASE-C727` |  | función | sqrt |  | 1–1 argumentos |
| `PROFUNDELASE-82E5` |  | función | exp |  | 1–1 argumentos |
| `PROFUNDELASE-CC29` |  | función | log | natural log, or log(x, base) | 1–2 argumentos |
| `PROFUNDELASE-B9BE` |  | función | log10 |  | 1–1 argumentos |
| `PROFUNDELASE-5D1D` |  | función | sin |  | 1–1 argumentos |
| `PROFUNDELASE-C6E2` |  | función | cos |  | 1–1 argumentos |
| `PROFUNDELASE-903B` |  | función | tan |  | 1–1 argumentos |
| `PROFUNDELASE-1802` |  | función | asin |  | 1–1 argumentos |
| `PROFUNDELASE-9998` |  | función | acos |  | 1–1 argumentos |
| `PROFUNDELASE-DDFF` |  | función | atan |  | 1–1 argumentos |
| `PROFUNDELASE-3819` |  | función | atan2 |  | 2–2 argumentos |
| `PROFUNDELASE-270F` |  | función | hypot |  | 2–2 argumentos |
| `PROFUNDELASE-D056` |  | función | degrees |  | 1–1 argumentos |
| `PROFUNDELASE-CED2` |  | función | radians |  | 1–1 argumentos |
| `PROFUNDELASE-CF8B` |  | función | sind | sine of an angle in degrees | 1–1 argumentos |
| `PROFUNDELASE-6291` |  | función | cosd | cosine of an angle in degrees | 1–1 argumentos |
| `PROFUNDELASE-894E` |  | función | tand | tangent of an angle in degrees | 1–1 argumentos |
| `PROFUNDELASE-9842` |  | función | asind | arc sine in degrees | 1–1 argumentos |
| `PROFUNDELASE-DBDC` |  | función | acosd | arc cosine in degrees | 1–1 argumentos |
| `PROFUNDELASE-BD6F` |  | función | atand | arc tangent in degrees | 1–1 argumentos |
| `PROFUNDELASE-8A75` |  | función | atan2d | atan2(y, x) in degrees | 2–2 argumentos |
| `PROFUNDELASE-8BB7` |  | función | is_undefined | 1 if the value is #N/A (NA), none or not a number | 1–1 argumentos |
| `PROFUNDELASE-E0CB` |  | función | int |  | 1–1 argumentos |
| `PROFUNDELASE-56D7` |  | función | float |  | 1–1 argumentos |
| `PROFUNDELASE-18C8` |  | función | bool |  | 1–1 argumentos |
| `PROFUNDELASE-FF6C` |  | función | str |  | 1–1 argumentos |
| `PROFUNDELASE-D512` |  | función | sign | -1, 0 or 1 | 1–1 argumentos |
| `PROFUNDELASE-EF37` |  | función | clamp | clamp(x, low, high) | 3–3 argumentos |
| `PROFUNDELASE-0323` |  | función | len | length of a list or text | 1–1 argumentos |
| `PROFUNDELASE-E70B` |  | función | sum | sum of a list | 1–1 argumentos |
| `PROFUNDELASE-4F09` |  | función | mean | mean of a list | 1–1 argumentos |
| `PROFUNDELASE-EBBD` |  | función | sorted |  | 1–1 argumentos |
| `PROFUNDELASE-5364` |  | función | reversed |  | 1–1 argumentos |
| `PROFUNDELASE-4E6B` |  | función | index | position of a value (-1 if absent) | 2–2 argumentos |
| `PROFUNDELASE-79EE` |  | función | count | occurrences of a value in a list | 2–2 argumentos |
| `PROFUNDELASE-6BE1` |  | función | array | array(n, fill=0): a list of n values | 1–2 argumentos |
| `PROFUNDELASE-DB5C` |  | función | range | list of integers | 1–3 argumentos |
| `PROFUNDELASE-CAFD` |  | función | random | uniform random number in [0, 1) | 0–0 argumentos |
| `PROFUNDELASE-2869` |  | función | uniform | uniform random number in [a, b] | 2–2 argumentos |
| `PROFUNDELASE-8DB2` |  | función | randint | random integer a..b (inclusive) | 2–2 argumentos |
| `PROFUNDELASE-CF37` |  | función | gauss | normal random number (mean, sd) | 2–2 argumentos |
| `PROFUNDELASE-08A5` |  | función | choice | random element of a list | 1–1 argumentos |
| `PROFUNDELASE-31E8` |  | función | shuffle | shuffled copy of a list; shuffle(list, n): at most n equal values in a row | 1–2 argumentos |
| `PROFUNDELASE-898C` |  | función | time | test time (s) | 0–0 argumentos |
| `PROFUNDELASE-8078` |  | función | zone | 1 if the animal's centre is in the zone | 1–1 argumentos |
| `PROFUNDELASE-80B8` |  | función | head_zone | 1 if the head is in the zone | 1–1 argumentos |
| `PROFUNDELASE-1F20` |  | función | zone_time | total time in the zone so far (s) | 1–1 argumentos |
| `PROFUNDELASE-4AC7` |  | función | zone_entries | entries into the zone so far | 1–1 argumentos |
| `PROFUNDELASE-FC2B` |  | función | detected | 1 if the animal is detected | 0–0 argumentos |
| `PROFUNDELASE-9756` |  | función | freezing | 1 while freezing | 0–0 argumentos |
| `PROFUNDELASE-F18E` |  | función | immobile | 1 while immobile | 0–0 argumentos |
| `PROFUNDELASE-4C0C` |  | función | speed | current speed | 0–0 argumentos |
| `PROFUNDELASE-972A` |  | función | distance | distance travelled | 0–0 argumentos |
| `PROFUNDELASE-9335` |  | función | x | x position | 0–0 argumentos |
| `PROFUNDELASE-0523` |  | función | y | y position | 0–0 argumentos |
| `PROFUNDELASE-2793` |  | función | input | input([device,] channel): current input value | 1–2 argumentos |
| `PROFUNDELASE-9812` |  | función | output | output([device,] channel): current output value | 1–2 argumentos |
| `PROFUNDELASE-4073` |  | función | analog | analog([device,] channel) | 1–2 argumentos |
| `PROFUNDELASE-3E8B` |  | función | encoder | encoder([device,] channel) | 1–2 argumentos |
| `PROFUNDELASE-BFE4` |  | función | activations | activations([device,] channel): times the input switched on | 1–2 argumentos |
| `PROFUNDELASE-CFA4` |  | función | pellets | pellets dispensed (all, or of a [device,] channel) | 0–2 argumentos |
| `PROFUNDELASE-947A` |  | función | timer | timer value (s) | 1–1 argumentos |
| `PROFUNDELASE-2FFA` |  | función | switch | virtual switch state | 1–1 argumentos |
| `PROFUNDELASE-05DE` |  | función | key | 1 while the key is held down | 1–1 argumentos |
| `PROFUNDELASE-065F` |  | función | responses | responses registered on a schedule | 1–1 argumentos |
| `PROFUNDELASE-F8BE` |  | función | reinforcers | reinforcers earned on a schedule | 1–1 argumentos |
| `PROFUNDELASE-E34A` |  | función | requirement | current requirement of a schedule | 1–1 argumentos |
| `PROFUNDELASE-E949` |  | función | test_running | 1 while the test runs (not paused, not waiting to start) | 0–0 argumentos |
| `PROFUNDELASE-D579` |  | función | test_paused | 1 while the test is paused | 0–0 argumentos |
| `PROFUNDELASE-82E6` |  | función | stage | the test's stage | 0–0 argumentos |
| `PROFUNDELASE-1FDA` |  | función | trial | the test's trial number | 0–0 argumentos |
| `PROFUNDELASE-E9C7` |  | función | apparatus | the test's apparatus | 0–0 argumentos |
| `PROFUNDELASE-951E` |  | función | treatment | the animal's treatment group (its code when testing blind) | 0–0 argumentos |
| `PROFUNDELASE-950F` |  | función | animal | the animal's number (id) | 0–0 argumentos |
| `PROFUNDELASE-FA50` |  | función | animal_field | animal_field('Sex'): a value from the animal's information | 1–1 argumentos |
| `PROFUNDELASE-30F4` |  | función | date | today's date, 'YYYY-MM-DD' | 0–0 argumentos |
| `PROFUNDELASE-0D81` |  | función | time_of_day | the clock time in seconds since midnight | 0–0 argumentos |
| `PROFUNDELASE-D096` |  | función | freezing_time | total freezing time so far (s) | 0–0 argumentos |
| `PROFUNDELASE-BEA4` |  | función | immobile_time | total immobile time so far (s) | 0–0 argumentos |
| `PROFUNDELASE-39A5` |  | función | zone_distance | distance from the animal's centre to a zone (0 inside it) | 1–1 argumentos |
| `PROFUNDELASE-903A` |  | función | point_distance | distance from the animal's centre to a point | 1–1 argumentos |
| `PROFUNDELASE-7F70` |  | función | head_x | head x position | 0–0 argumentos |
| `PROFUNDELASE-EE68` |  | función | head_y | head y position | 0–0 argumentos |
| `PROFUNDELASE-BCB5` |  | función | tail_x | tail x position | 0–0 argumentos |
| `PROFUNDELASE-FC85` |  | función | tail_y | tail y position | 0–0 argumentos |
| `PROFUNDELASE-576D` |  | función | x_percent | x position as % of the arena's width (0 = left) | 0–0 argumentos |
| `PROFUNDELASE-B7C0` |  | función | y_percent | y position as % of the arena's height (0 = top) | 0–0 argumentos |
| `PROFUNDELASE-0E81` |  | función | sequence_duration | duration of the last completed run of an apparatus zone sequence (s) | 1–1 argumentos |
| `PROFUNDELASE-0122` |  | función | speaker | speaker([device]): 1 while a sound is playing | 0–1 argumentos |
| `PROFUNDELASE-8351` |  | función | output_volts | output_volts([device,] channel): analogue output level in volts | 1–2 argumentos |
| `PROFUNDELASE-3AE4` |  | función | rearing | 1 while the animal rears | 0–0 argumentos |
| `PROFUNDELASE-AECF` |  | función | rears | rears so far | 0–0 argumentos |
| `PROFUNDELASE-7DD9` |  | función | rearing_time | total time rearing so far (s) | 0–0 argumentos |
| `PROFUNDELASE-3DDD` |  | función | investigating | 1 while the animal investigates the zone | 1–1 argumentos |
| `PROFUNDELASE-F389` |  | función | orientation | angle (°) between the body's orientation and the direction to a zone or point (-1: unknown) | 1–1 argumentos |
| `PROFUNDELASE-82C9` |  | función | zone_label | the label set on a zone (or '') | 1–1 argumentos |

## Estadística: las 47 pruebas (cada una al menos una vez)

Cómo llegar: Results ▸ Statistics (ver ESPERADO_ESTADISTICA.md)

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `ESTLAS47PRUC-75CA` | two groups | prueba | Student's t-test |  |  |
| `ESTLAS47PRUC-A991` | two groups | prueba | Welch's t-test |  |  |
| `ESTLAS47PRUC-9F8F` | two groups | prueba | Paired t-test |  |  |
| `ESTLAS47PRUC-3CEE` | two groups | prueba | Mann-Whitney U |  |  |
| `ESTLAS47PRUC-7565` | two groups | prueba | Wilcoxon signed-rank |  |  |
| `ESTLAS47PRUC-844F` | two groups | prueba | Kolmogorov-Smirnov (two-sample) |  |  |
| `ESTLAS47PRUC-56B3` | two groups | prueba | Brunner-Munzel |  |  |
| `ESTLAS47PRUC-A9FA` | one sample | prueba | One-sample t-test |  |  |
| `ESTLAS47PRUC-03C7` | one sample | prueba | Wilcoxon signed-rank vs value |  |  |
| `ESTLAS47PRUC-5141` | several groups | prueba | One-way ANOVA |  |  |
| `ESTLAS47PRUC-460D` | several groups | prueba | Welch's ANOVA |  |  |
| `ESTLAS47PRUC-5B89` | several groups | prueba | Alexander-Govern |  |  |
| `ESTLAS47PRUC-52CE` | several groups | prueba | Kruskal-Wallis |  |  |
| `ESTLAS47PRUC-3A82` | several groups | prueba | Mood's median test |  |  |
| `ESTLAS47PRUC-58E2` | repeated measures | prueba | Repeated-measures ANOVA |  |  |
| `ESTLAS47PRUC-5731` | repeated measures | prueba | Friedman |  |  |
| `ESTLAS47PRUC-5A39` | two factors | prueba | Two-way ANOVA |  |  |
| `ESTLAS47PRUC-B2CD` | two factors | prueba | Mixed two-way ANOVA |  |  |
| `ESTLAS47PRUC-C631` | two factors | prueba | Scheirer-Ray-Hare |  |  |
| `ESTLAS47PRUC-D86B` | two factors | prueba | Aligned rank transform ANOVA |  |  |
| `ESTLAS47PRUC-A203` | two factors | prueba | ANCOVA (analysis of covariance) |  |  |
| `ESTLAS47PRUC-AE76` | post-hoc | prueba | Tukey HSD |  |  |
| `ESTLAS47PRUC-D5CA` | post-hoc | prueba | Bonferroni |  |  |
| `ESTLAS47PRUC-CE3C` | post-hoc | prueba | Holm |  |  |
| `ESTLAS47PRUC-1AD2` | post-hoc | prueba | Šidák |  |  |
| `ESTLAS47PRUC-4EA9` | post-hoc | prueba | Benjamini-Hochberg (FDR) |  |  |
| `ESTLAS47PRUC-17B3` | post-hoc | prueba | Dunnett (vs control) |  |  |
| `ESTLAS47PRUC-5D8E` | post-hoc | prueba | Games-Howell |  |  |
| `ESTLAS47PRUC-B115` | post-hoc | prueba | Dunn |  |  |
| `ESTLAS47PRUC-DD01` | post-hoc | prueba | Duncan's multiple range test |  |  |
| `ESTLAS47PRUC-3783` | post-hoc | prueba | Fisher's LSD |  |  |
| `ESTLAS47PRUC-06D3` | post-hoc | prueba | Scheffé's test |  |  |
| `ESTLAS47PRUC-8FE7` | post-hoc | prueba | Student-Newman-Keuls |  |  |
| `ESTLAS47PRUC-7350` | categorical | prueba | Chi-square test of independence |  |  |
| `ESTLAS47PRUC-BC3D` | categorical | prueba | Fisher's exact test |  |  |
| `ESTLAS47PRUC-C87C` | categorical | prueba | G-test (log-likelihood ratio) |  |  |
| `ESTLAS47PRUC-ECDD` | categorical | prueba | Chi-square goodness of fit |  |  |
| `ESTLAS47PRUC-57F1` | correlation | prueba | Pearson correlation |  |  |
| `ESTLAS47PRUC-1EE5` | correlation | prueba | Spearman correlation |  |  |
| `ESTLAS47PRUC-1EEA` | correlation | prueba | Kendall's tau |  |  |
| `ESTLAS47PRUC-07D8` | correlation | prueba | Linear regression |  |  |
| `ESTLAS47PRUC-7B00` | assumptions | prueba | Shapiro-Wilk |  |  |
| `ESTLAS47PRUC-A3C4` | assumptions | prueba | D'Agostino-Pearson |  |  |
| `ESTLAS47PRUC-ADFB` | assumptions | prueba | Levene |  |  |
| `ESTLAS47PRUC-7323` | assumptions | prueba | Brown-Forsythe |  |  |
| `ESTLAS47PRUC-B662` | assumptions | prueba | Bartlett |  |  |
| `ESTLAS47PRUC-D3E6` | assumptions | prueba | Fligner-Killeen |  |  |

## Mensajes que aparecen al pulsar sin preparar datos (comprueba que sean claros)

Cómo llegar: pulsando lo indicado con el demo recién abierto

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `MENQUEAPAALP-C3F0` |  | mensaje | critical: Open experiment: “tmpd6g_ypht” is not a mANY-MAZE experiment: it has no experiment file (project.json). Choose the experiment's own folder (its name usually ends in .mmaze), or create one wi | aparece al pulsar: menú File ▸ Open experiment… |  |
| `MENQUEAPAALP-D27B` |  | mensaje | information: Restore a backup: This experiment has no backups yet. A backup of the experiment file is made when it is saved (at most every 10 minutes). | aparece al pulsar: menú File ▸ Restore a backup… |  |
| `MENQUEAPAALP-87E7` |  | mensaje | question: Apply template: Make this a Open field protocol with tests of 600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Open field» |  |
| `MENQUEAPAALP-F972` |  | mensaje | question: Apply template: Make this a Elevated plus maze protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Elevated plus maze» |  |
| `MENQUEAPAALP-E280` |  | mensaje | question: Apply template: Make this a Elevated zero maze protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Elevated zero maze» |  |
| `MENQUEAPAALP-39B1` |  | mensaje | question: Apply template: Make this a Y maze (spontaneous alternation) protocol with tests of 480 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Y maze (spontaneous alternation)» |  |
| `MENQUEAPAALP-4004` |  | mensaje | question: Apply template: Make this a T maze protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «T maze» |  |
| `MENQUEAPAALP-6488` |  | mensaje | question: Apply template: Make this a Radial arm maze protocol with tests of 600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Radial arm maze» |  |
| `MENQUEAPAALP-B9F4` |  | mensaje | question: Apply template: Make this a Radial arm place conditioning (RAPC) protocol with tests of 600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Radial arm place conditioning (RAPC)» |  |
| `MENQUEAPAALP-DC1A` |  | mensaje | question: Apply template: Make this a Morris water maze protocol with tests of 60 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Morris water maze» |  |
| `MENQUEAPAALP-E0D0` |  | mensaje | question: Apply template: Make this a Barnes maze protocol with tests of 180 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Barnes maze» |  |
| `MENQUEAPAALP-C80F` |  | mensaje | question: Apply template: Make this a Novel object recognition protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Novel object recognition» |  |
| `MENQUEAPAALP-0CEE` |  | mensaje | question: Apply template: Make this a Light/dark box protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Light/dark box» |  |
| `MENQUEAPAALP-CC86` |  | mensaje | question: Apply template: Make this a Three-chamber sociability protocol with tests of 600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Three-chamber sociability» |  |
| `MENQUEAPAALP-01BC` |  | mensaje | question: Apply template: Make this a Fear conditioning / freezing protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Fear conditioning / freezing» |  |
| `MENQUEAPAALP-FE06` |  | mensaje | question: Apply template: Make this a Forced swim test (Porsolt) protocol with tests of 360 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Forced swim test (Porsolt)» |  |
| `MENQUEAPAALP-582E` |  | mensaje | question: Apply template: Make this a Tail suspension test protocol with tests of 360 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Tail suspension test» |  |
| `MENQUEAPAALP-C048` |  | mensaje | question: Apply template: Make this a Novel tank diving test (fish) protocol with tests of 360 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Novel tank diving test (fish)» |  |
| `MENQUEAPAALP-3AB4` |  | mensaje | question: Apply template: Make this a Multi-well plate (larvae) protocol with tests of 600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Multi-well plate (larvae)» |  |
| `MENQUEAPAALP-E8DF` |  | mensaje | question: Apply template: Make this a Conditioned place preference protocol with tests of 900 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Conditioned place preference» |  |
| `MENQUEAPAALP-6BB7` |  | mensaje | question: Apply template: Make this a Hole board protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Hole board» |  |
| `MENQUEAPAALP-2F12` |  | mensaje | question: Apply template: Make this a Thermal gradient ring protocol with tests of 3600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Thermal gradient ring» |  |
| `MENQUEAPAALP-C2D1` |  | mensaje | question: Apply template: Make this a Home cage protocol with tests of 3600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Home cage» |  |
| `MENQUEAPAALP-66D4` |  | mensaje | question: Apply template: Make this a Activity wheel / circular runway protocol with tests of 3600 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Activity wheel / circular runway» |  |
| `MENQUEAPAALP-87B7` |  | mensaje | question: Apply template: Make this a Custom protocol with tests of 300 s? Existing results are recalculated with the new protocol type. | aparece al pulsar: Protocol: cinta «Apply template» ▸ «Custom» |  |
| `MENQUEAPAALP-A797` |  | mensaje | question: Restore defaults: Restore the default animal tracking settings? | aparece al pulsar: Protocol ▸ Animal tracking: cinta «Restore defaults» |  |
| `MENQUEAPAALP-00BE` |  | mensaje | critical: Convert DeepLabCut model: “track_dlc.csv” is not a PyTorch model checkpoint. Choose the snapshot file (.pt) that DeepLabCut saved when training the model, in the model's train folder. | aparece al pulsar: Protocol ▸ Animal tracking: botón «Convert DeepLabCut model…» |  |
| `MENQUEAPAALP-800E` |  | mensaje | question: Delete key: The key “Rearing” has 6 scored events in 6 tests. Delete the key? The events stay in the tests but are no longer analysed (a key of the same name analyses them again). | aparece al pulsar: Protocol ▸ Keys: cinta «Delete key» |  |
| `MENQUEAPAALP-A9C9` |  | mensaje | question: Restore defaults: Restore the default analysis settings? | aparece al pulsar: Protocol ▸ Analysis: cinta «Restore defaults» |  |
| `MENQUEAPAALP-C269` |  | mensaje | question: Delete apparatus: Delete the apparatus “Open field”? 6 test(s) use it and will have no apparatus. | aparece al pulsar: Apparatus: cinta «Delete» |  |
| `MENQUEAPAALP-1904` |  | mensaje | information: Training criteria: No training criteria are defined. Add them on the Protocol (Training criteria). | aparece al pulsar: Experiment: cinta «Training criteria» |  |
| `MENQUEAPAALP-65FA` |  | mensaje | question: Delete treatment: Delete the treatment “Control”? 3 animals will be left without a treatment. | aparece al pulsar: Experiment ▸ Treatments: cinta «Delete treatment» |  |
| `MENQUEAPAALP-0E4D` |  | mensaje | information: Join video files: Choose at least two video files. | aparece al pulsar: Test schedule: cinta «Add tests from videos» ▸ «Join video files into one test…» |  |
| `MENQUEAPAALP-D707` |  | mensaje | question: Delete tests: Delete test 1 (C1, Day 1 trial 1) and its tracks? Video files are not deleted. | aparece al pulsar: Test schedule: cinta «Delete» |  |
| `MENQUEAPAALP-3680` |  | mensaje | question: Clear tracks: Delete the tracks of 1 test? Videos and scored events are kept. | aparece al pulsar: Test schedule: cinta «Clear tracks» |  |
| `MENQUEAPAALP-44FE` |  | mensaje | question: End stage for animal: End the stage for C1 · Day 1? Their remaining tests of the stage are skipped and new schedules leave the stage out. Use “Reopen stage for animal” to undo. | aparece al pulsar: Test schedule: cinta «End stage for animal» |  |
| `MENQUEAPAALP-985C` |  | mensaje | information: Run tests: Add a camera or a video file first (Add camera / video source). | aparece al pulsar: Run tests: cinta «Add test panel» |  |
| `MENQUEAPAALP-4742` |  | mensaje | information: E-mail report: No e-mail server is set up. Add an Alerts (e-mail / SMS) device with its SMTP server in Protocol ▸ Hardware ▸ I/O devices: reports are sent through it, as the alerts are. | aparece al pulsar: Data ▸ Spreadsheet: cinta «E-mail report» |  |

## Diálogos que deben abrirse al menos una vez

Cómo llegar: desde el botón o menú que los abre

| ID | Grupo | Tipo | Control | Detalles | Notas |
|---|---|---|---|---|---|
| `DIQUEDEBABRA-E5B7` |  | diálogo | Add animals | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-8AC4` |  | diálogo | Add grid | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-2556` |  | diálogo | Add tests from videos | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-E536` |  | diálogo | Adjust apparatus | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-81D0` |  | diálogo | Adjust calibration | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-487F` |  | diálogo | Allowed programs | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-E009` |  | diálogo | Analysis plug-in: … | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-107A` |  | diálogo | Calibrate | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-2425` |  | diálogo | Calibrate … | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-A566` |  | diálogo | Check video | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-D1D3` |  | diálogo | Convert DeepLabCut model | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-452B` |  | diálogo | Create apparatus from template | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-56FE` |  | diálogo | Create test schedule | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-D604` |  | diálogo | Dose calculator | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-C3DA` |  | diálogo | E-mail report | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-90DE` |  | diálogo | Export video with overlays | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-3A57` |  | diálogo | Expression functions | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-DCF8` |  | diálogo | HTML report | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-9150` |  | diálogo | I/O devices | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-F531` |  | diálogo | Import DeepLabCut track | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-4E8E` |  | diálogo | Industrial cameras | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-BB36` |  | diálogo | Install pose model | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-5096` |  | diálogo | New experiment | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-F6D5` |  | diálogo | Operant chambers | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-F42E` |  | diálogo | Panel settings | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-B0C8` |  | diálogo | Procedure “…” as JSON | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-FED8` |  | diálogo | Randomise treatments | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-64F8` |  | diálogo | Recorded video file names | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-0BFA` |  | diálogo | Test variables | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-98B7` |  | diálogo | Touch screen | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-F8C6` |  | diálogo | Training criteria | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-41E7` |  | diálogo | Users and security | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-0659` |  | diálogo | Weigh animal … before test … | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-0BC9` |  | diálogo | Weigh animals | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
| `DIQUEDEBABRA-A200` |  | diálogo | mANY-MAZE touch screen | abrir, revisar cada control, Cancelar y luego usarlo de verdad | NO se abrió solo: hace falta preparar datos para llegar |
| `DIQUEDEBABRA-6FFD` |  | diálogo | mANY-MAZE user guide | abrir, revisar cada control, Cancelar y luego usarlo de verdad | abierto en la exploración automática |
