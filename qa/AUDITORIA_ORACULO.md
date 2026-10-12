# Auditoría independiente del oráculo de medidas

Fecha: 2026-10-11. Alcance: `qa/herramientas/oraculo_medidas.py`, `qa/herramientas/oraculo_funciones.py` y, solo en
la parte que define valores esperados de las medidas específicas de cada prueba (alternancia, errores, laberinto
acuático, Barnes, NOR, CPP…), `qa/herramientas/oraculo_sintetico.py` (líneas ≈ 200–700, 1040–1090). Es código del
oráculo, no de la app, y era necesario para cubrir la alternancia, los errores y Whishaw.

No se ha leído código de medidas de la app (`manymaze/core/measures*`, `analysis*`…). Las fuentes son la guía
(`manymaze/resources/USER_GUIDE.md`, §1 *Protocol tab*, §5 *How tracking works*, §9 *Measures* y *Time periods*),
`qa/HALLAZGOS_PREVIOS.md` (H-P05, H-P06), la ayuda publicada de ANY-maze (versión 7.62, descargada el 2026-10-11) y la
literatura citada al final. No se ha cambiado el oráculo ni la app y no hay commit.

Veredictos:

- **correcto**: coincide con la guía y con la práctica habitual (o con ANY-maze cuando la guía dice «as ANY-maze»).
- **dudoso**: hay dos lecturas defendibles, o la guía no lo fija y el oráculo tomó el valor de la app.
- **incorrecto**: la guía, ANY-maze o la literatura contradicen lo que codifica el oráculo (en la variante
  señalada).

`doc|impl` es la notación del propio oráculo. Si el veredicto afecta solo a una de las dos variantes, se indica.

---

## 0. Problemas de método (riesgo de autoconfirmación)

1. **Los ajustes por defecto que da por supuestos el oráculo no están en la guía.** Son: inmóvil < 2 cm/s durante
   ≥ 2 s; freezing ≥ 1 s; suavizado de 0,2 s; latencia de lo que no ocurre = duración; banda de tigmotaxis = 25 %
   del semiancho. La guía no da ninguno de estos valores (solo nombra los ajustes: «mobility threshold»,
   «*When an event never occurs, its latency is*»…). Por tanto salen de la app, y el oráculo hereda sus decisiones.
   ANY-maze usa otros valores por defecto:
   - freezing mínimo de **2 s**;
   - latencia de un evento que no ocurre **indefinida**, salvo que se marque una opción;
   - inmovilidad **sin umbral de velocidad**: es el % del cuerpo que sigue en el mismo sitio (ver 1.1).
2. **Varias variantes «doc» no son la lectura literal de la guía:**
   - *Time moving towards/away*: la guía dice «mobile», que en la guía es el **estado** móvil. Eso es la variante
     `impl`, no la `doc` (cuadro a cuadro).
   - *Time facing*: la guía no dice desde dónde se mide. ANY-maze lo mide desde la **cabeza**, que es la variante
     `impl`.
   - *Whishaw corridor distance* del laberinto acuático: la nota del oráculo llama «doc» a contar el paso que
     **entra**. La guía dice lo contrario para el corredor de una zona, y ANY-maze también.

   Con estas etiquetas, la definición que la app eligió aparece como «doc» o como alternativa aceptable, cuando no
   lo es.
3. **La capa B no puede detectar errores de definición.** Aplica las mismas fórmulas al track de la app. Solo
   separa los errores del rastreo de los del cálculo. Por eso H-P05 dice que «el cálculo es correcto»: es correcto
   *respecto a la misma definición*.
4. **Hay alternativas demasiado laxas.** Por ejemplo, *Cumulative distance to platform* acepta cuatro valores, y
   *Mean distance to platform* y *Whishaw path %* aceptan dos cada una. Con eso el oráculo no fija la definición, y
   cualquiera de ellas pasa como «ALTERNATIVA».
5. **El oráculo es incoherente consigo mismo en el ángulo de «mirar a»:**
   - investigación: vector centro→cabeza frente a la dirección **desde el centro** al centro de la zona;
   - exploración de puntos: frente a la dirección **desde la cabeza** al punto;
   - NOR en `oraculo_sintetico`: el cuerpo frente a la dirección al objeto.

   Puede ser correcto en cada caso, pero nadie lo ha decidido explícitamente.
6. **Lo que el oráculo no comprueba:** inmovilidad, freezing, giros y rotaciones **por intervalo**. Los episodios
   que cruzan el límite de un intervalo son justo donde suele haber errores, y `periods()` solo comprueba duración,
   distancia y, por zona, tiempo, entradas y latencia. Además, la distancia de cada periodo, `step[ia+1:ib]`,
   pierde el paso que cruza el límite. Es un defecto menor del propio oráculo: la suma de los intervalos no da el
   total.

---

## 1. Tabla de medidas

### 1.1 Movilidad, inmovilidad, actividad, velocidad

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico de laboratorio |
|---|---|---|---|---|
| Distancia total | Suma de los pasos entre posiciones suavizadas con media móvil de 0,2 s | dudoso | Guía §5.7 («optional smoothing»), §9 «head distance (smoothed like the centre)». ANY-maze: suavizado **adaptativo** según el tamaño del animal (*Total distance travelled*) | La ventana de 0,2 s no está en la guía y cambia todas las distancias y velocidades. Que el valor por defecto quede documentado |
| Velocidad media | Distancia / duración | correcto | ANY-maze *Average speed* | Igual |
| Velocidad máxima | doc: máximo de la velocidad por cuadro. impl: esa velocidad promediada otra vez 0,2 s | dudoso | ANY-maze *Maximum speed*: «the speed … between positions … maximum» (la doc). ANY-maze reconoce que su velocidad instantánea antigua «was a little exaggerated» (*Analysis options*) | Un pico de un cuadro (un salto) decide el valor. Esperaría una ventana documentada (p. ej. 0,5–1 s) |
| Móvil / inmóvil (tiempo, episodios, media, más largo/corto, latencias) | Velocidad < 2 cm/s; los tramos inmóviles de < 2 s pasan a móviles. impl: la velocidad, además, promediada 0,5 s | **dudoso (doc e impl)**. **incorrecto** si se presenta como «as ANY-maze» | La guía **no define** la inmovilidad por velocidad. ANY-maze (*Immobility detection*, T0358): «the percentage of the animal which has to remain in the same place» (sensibilidad 50–100 %, ≈ 70 %) durante un tiempo mínimo. No hay umbral de velocidad. EthoVision sí usa velocidad con promedio | Que «tiempo inmóvil» sea comparable con ANY-maze (no lo es: es otro criterio). Que un pico de ruido de un cuadro no parta un episodio. La doc lo parte; la impl lo amortigua, más cerca de la práctica de EthoVision. H-P05: a 6,9 px/cm el temblor supera los 2 cm/s y el tiempo inmóvil cae un 18 % |
| Episodios al empezar | Se cuenta un episodio inmóvil (móvil) si la prueba empieza inmóvil (móvil) | correcto | ANY-maze *Total immobile episodes*: «assumed to be mobile at the start»; guía §9 *Activity* | Igual |
| Latencia al último episodio | Inicio del último tramo; no se añade si no hay ninguno | correcto | ANY-maze *Latency to start of last …* («#N/A» si nunca); guía §9 | Igual |
| Latencia al primer episodio que nunca ocurre | = duración | dudoso | La guía remite a un ajuste sin decir su valor por defecto. ANY-maze: «usually … undefined»; la duración es una opción (*Analysis options*) | En el laberinto acuático es habitual tomar el máximo de la prueba (60 s) como latencia de escape (Vorhees & Williams 2006). Para otras latencias, muchos laboratorios prefieren dejarla en blanco |
| Velocidad media estando móvil | Distancia estando móvil / tiempo móvil | correcto | Guía §9. ANY-maze lo sugiere como cálculo | Igual |
| Activo / inactivo | Activo = móvil (sin teclas que cuenten como actividad) | correcto | Guía §9 *Activity*, §1. ANY-maze T0358 «mobility is one form of activity» | Igual |
| Zona: tiempo móvil / inmóvil / episodios inmóviles | Cuadros dentro y en ese estado; se cuenta un episodio al entrar ya inmóvil | correcto | Guía §9 *Per zone* | Igual |

### 1.2 Freezing

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Tiempo, %, episodios, latencia, media/más largo/más corto | Capa A: completamente quieto (sin desplazarse ni girar ni cambiar de tamaño) ≥ 1 s. Capa B: movimiento < 2 % del cuerpo, termina > 3 % (histéresis) y ≥ 1 s | correcto (criterio). **dudoso** (duración mínima de 1 s) | Guía §1 (*Freezing starts / ends*). Fanselow 1980; Anagnostaras et al. 2010 (VideoFreeze, ≥ 1 s = 30 cuadros). ANY-maze (*Freezing detection*, T0359): mínimo **2000 ms** por defecto, y el episodio empieza en el inicio real | 1 s es lo habitual en condicionamiento del miedo. Quien compare con ANY-maze tendrá más freezing (episodios de 1–2 s). Que la guía diga el valor por defecto |
| % freezing | Tiempo / duración | correcto | Guía; práctica estándar | Igual |
| Zona: tiempo freezing / episodios | Cuadros congelado dentro / comienzos dentro | correcto respecto a la guía | Guía §9 (lo documenta). ANY-maze: suma el episodio entero «if the animal was in the zone» (ambiguo) | Aceptable; diferirá de ANY-maze en los episodios que cruzan el borde |

### 1.3 Giros, sinuosidad, rotaciones, cabeza

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Ángulo de giro absoluto | Σ\|cambio de rumbo\| entre cuadros con velocidad > 2 cm/s. A través de una parada, el giro se mide desde el último cuadro en movimiento | correcto respecto a la guía. Difiere de ANY-maze | Guía §9 («our choice»). ANY-maze *Absolute turn angle*: suma en **todas** las posiciones, sin filtro. EthoVision: el giro se dispara con desplazamientos mínimos («Meander can get high, unrealistic values») y recomienda una distancia mínima | Que el ruido de rastreo no lo infle. Con el umbral de 2 cm/s sobre posiciones suavizadas 0,2 s sigue entrando temblor: H-P05 mide ×1,9 en datos rastreados. Esperaría una **distancia mínima por paso** (como EthoVision), no solo un umbral de velocidad |
| Velocidad angular | Giro absoluto / duración de la prueba | correcto (= ANY-maze) | ANY-maze (nota de *Absolute turn angle*); guía §9 | Un usuario de EthoVision espera la media de la velocidad angular **mientras se mueve**, en °/s. Aquí sale mucho menor. Hay que documentarlo bien |
| Meander | Giro absoluto / distancia | correcto (= ANY-maze) | ANY-maze; guía | EthoVision lo calcula por muestra y promedia. Valores no comparables entre programas |
| Ángulo de giro medio | Media de los giros entre cuadros en movimiento | dudoso | No aparece en la guía ni en ANY-maze | Depende de la frecuencia de cuadros (a 50 fps sale la mitad que a 25). Esperaría «por cm» o «por s» |
| Rotaciones (horario, antihorario, total) | Vueltas de 360° de la orientación centro→cabeza. Un giro termina al volver > 90°. doc: la vuelta siguiente cuenta **desde el extremo**. impl: desde el punto en que se detecta la vuelta atrás (hasta 450°) | **doc correcta respecto al modo avanzado de ANY-maze. impl incorrecta.** El umbral de 90° es **dudoso** frente al ANY-maze por defecto | ANY-maze *Options for analysis of rotations* (T0654): por defecto la «reversal sensitivity is, in effect, 359.9°», es decir, una vuelta atrás parcial no corta la rotación. Con una sensibilidad menor, «start analysing a new rotation from 6», el extremo; el ejemplo 12→6→4→12 lo deja claro. H-P06 n.º 5 dice que la app usa la impl | En una prueba de rotación (6-OHDA, anfetamina; rotómetro de Ungerstedt & Arbuthnott 1970) se cuentan vueltas netas. Una regla de 90° **cuenta menos** vueltas en un animal que cabecea al girar. Contar desde el punto de detección exige 450° y cuenta aún menos |
| Rotaciones parciales | ≥ 90° sin completar una vuelta, como máximo una por tramo, contadas desde el extremo | correcto | ANY-maze T0654 («at most just a single partial rotation … for a single bout»); guía §9 | Igual |
| Rotaciones de la trayectoria | Las mismas reglas sobre el rumbo de desplazamiento | dudoso | Guía §9. H-P04 (marcha atrás ambigua) | Un animal quieto no debería sumar vueltas de trayectoria. H-P05: 0 → 5–6 con el temblor |
| Giro de la cabeza (absoluto, horario, antihorario) | doc: diferencia cuadro a cuadro de la orientación centro→cabeza, ignorando saltos > 90° (cambio cabeza/cola). impl: la dirección acumulada suavizada 0,2 s | doc **correcta** (= ANY-maze). impl **dudosa** | ANY-maze *Absolute / Clockwise head turn angle*: cuadro a cuadro, sin suavizar (no menciona los > 90°). Guía §9 *Head* | Los dos sufren H-P01: con la cabeza invertida en el 30–45 % de los cuadros de vídeo real, ignorar los saltos de > 90° no basta. La medida no es fiable en vídeo real hasta que H-P01 esté cerrado |
| Distancia de la cabeza | Pasos de la nariz suavizada 0,2 s | correcto | Guía §9 | Igual |
| Zona: giro absoluto | Giros cuyo cuadro anterior en movimiento también está dentro | correcto (diferencia menor) | ANY-maze *Absolute turn angle while in the zone* (cada posición dentro) | Igual |
| Zona: giro de la cabeza | El giro cuenta en la zona del segundo cuadro | correcto | ANY-maze («It is the second of these two positions…») | Igual |

### 1.4 Posición, trayectoria, zonas

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Eficiencia y tortuosidad de la trayectoria | Recta inicio→fin / recorrido, y su inversa | correcto | ANY-maze *Path efficiency*; Benhamou 2004 | Igual |
| Posición X/Y media (%) | Media de las posiciones (ponderada por tiempo), % desde la izquierda/arriba | correcto | ANY-maze *Average X position*; guía | Igual |
| Distancia al centro / a la pared | Euclídea al centroide de la arena / al contorno (solo dentro) | correcto | Guía §9 | Igual |
| Tigmotaxis (%) | Dentro y a ≤ 25 % del semiancho de la arena desde la pared | dudoso | La guía no da el ancho de la banda. Treit & Fundytus 1988; Simon et al. 1994 usan bandas fijas (≈ 8–10 cm) | Que la banda sea un ajuste visible en cm. 25 % del semiancho es solo el 12,5 % del ancho |
| Transiciones entre zonas | doc: cambios de la zona más pequeña, incluidos los tramos fuera de toda zona. impl: sin esos tramos | doc **correcta** (análoga a ANY-maze). impl dudosa | ANY-maze *Number of line crossings*: cambios del «área única» del mapa, incluida la de fuera de toda zona. La guía no lo define (H-P06 n.º 8) | Que salir de A a «ninguna» y volver a A cuente. La impl lo oculta |
| Entradas en zona (empieza dentro) | Empezar dentro cuenta como entrada; la latencia es 0 | correcto | ANY-maze *Zone entry settings*: «ordinarily ANY-maze will count this as a zone entry»; guía §9 (es un ajuste) | En EPM, luz/oscuridad o Y, quien suelta al animal en el centro suele querer **no** contar esa entrada. Que el ajuste se vea en el protocolo |
| Entrada, en la literatura | Por el centro del cuerpo (por defecto) | dudoso (expectativa) | Pellow et al. 1985; Walf & Frye 2007 (EPM); Kraeuter et al. 2019 (Y): «las cuatro patas» | Una entrada por el centro da más entradas que la puntuación manual. Recomendar *Proportion of the body* en EPM, Y y radial |
| Visitas: media, más larga, más corta, lista; entradas/min; salidas; latencias | Tiempo / entradas; tramos; última entrada / salida | correcto | Guía §9 | Igual |
| Distancia en la zona | Pasos que salen de un cuadro dentro (no cuenta el de entrada, sí el de salida) | correcto | ANY-maze *Distance travelled in the zone* (nota y fig. 1); guía §9 | Igual |
| Distancia de la cabeza en la zona | doc: el paso que sale (como el centro). impl: el que llega | doc correcta; impl dudosa | Guía §9 (por analogía con el centro); H-P06 n.º 9 | Una sola regla para el centro y la cabeza |
| Distancia y eficiencia antes de la primera entrada | Pasos hasta el cuadro de entrada; recta / recorrido | correcto | ANY-maze *Analysis options* (eficiencia a la zona); guía | Igual |
| Distancia desde la zona (media, máx., mín., inicial, acumulada) | Al borde, 0 dentro; media sobre toda la prueba; mín. 0 una vez dentro | correcto respecto a la guía | Guía §9 | Igual |
| Distancia al borde estando dentro (centro); mín. 0 una vez ha salido | Igual que en la guía | correcto | Guía §9 («as ANY-maze») | Igual |
| Ídem, cabeza: mín. | doc: 0 una vez ha salido. impl: el mínimo estando dentro | doc correcta; impl dudosa | Guía §9 («as the centre's»); H-P06 n.º 12 | Coherencia con el centro |
| Entradas de la cabeza | Fuera→dentro; empezar dentro no cuenta | correcto | ANY-maze *Number of entries of the animal's head*; guía | Incoherente con las entradas del centro, pero es la definición de ANY-maze. Documentarlo |
| Latencia a la entrada de la cabeza si empieza y sigue dentro | = duración («nunca») | dudoso | Consecuencia de la regla anterior | Un científico leería «tardó toda la prueba» de un animal que estuvo dentro desde el principio. Mejor en blanco |
| Cabeza dentro con el centro fuera | Por posición, no por la regla de entrada | correcto | Guía §9 | Igual |
| *Time facing* (mirando a la zona) | doc: desde el **centro** a algún punto del borde, ≤ 30°. impl: desde la **cabeza** | **doc incorrecta; impl correcta** | ANY-maze *Time oriented towards the zone*: «A second vector from the animal's **head** position to a point on the border». La guía no lo dice (H-P06 n.º 4) | Desde la cabeza. La guía debería decirlo |
| Orientado al centro de la zona estando dentro | Cabeza→centro, ≤ 30° | correcto | ANY-maze (idem); guía | Igual |
| Acercándose / alejándose (*getting closer / further*) | Fuera, signo de la variación de la distancia al borde entre cuadros, sin umbral | **incorrecto frente a ANY-maze** (le falta el filtro); correcto respecto a la letra de la guía | ANY-maze *Time getting closer to the zone*: «very small movements of the animal will be ignored … based on the animal's size» | Que un animal quieto no se acerque ni se aleje. H-P05: +60–74 % en datos rastreados. Sobre la verdad sintética (sin temblor) el oráculo da el valor correcto, así que la capa A no ve el problema |
| Moviéndose hacia / alejándose (zona) | Fuera; rumbo a < 45° del centro de la zona (o de su opuesto). doc: cuadros con velocidad > umbral. impl: estado móvil | dudoso | Guía §9: «mobile, outside» (el «outside» es elección propia). ANY-maze: todas las posiciones, sin condición de movilidad, ±45° y excluye los casos ambiguos | La impl (H-P06 n.º 6) cuenta como «hacia» o «lejos» el rumbo del temblor en las paradas de < 2 s. La doc (velocidad por cuadro) es la que tiene sentido biológico |
| Error de rumbo absoluto medio | Media sobre los cuadros en movimiento fuera | correcto respecto a la guía | Guía §9 (justifica la diferencia con ANY-maze) | Igual |
| Error de rumbo inicial (zona, punto, laberinto acuático) | Primera posición → posición tras 1 s móvil (posiciones suavizadas). Si se ha desplazado < 0,5 cm, capa A: el primer desplazamiento > 0,5 cm; capa B: no se compara | dudoso | Guía §1/§9; ANY-maze *Initial heading error* (igual: «positions … while … immobile are ignored»). El arreglo de 0,5 cm es **del oráculo**, no de la guía (H-P07) | En el laberinto acuático el rumbo se mide a una distancia fija de la suelta. El modo «tras 1 s móvil» da un ángulo aleatorio si el animal se para menos de 2 s. Recomendar «por distancia» por defecto |
| Error de rumbo inicial con signo | Positivo = zona a la derecha (coordenadas de imagen, y hacia abajo) | correcto | ANY-maze; guía | Igual |
| CIPL | Distancia **al borde** de la zona cada 1 s desde el inicio hasta la primera entrada, menos la de un camino recto. doc: a la velocidad media **de toda la prueba**. impl: la de **hasta la entrada** | **doc incorrecta; impl correcta** | ANY-maze *Corrected integrated path length*: «mean speed prior to entering the zone», o bien con la opción, recorrido **y** velocidad de toda la prueba. La doc mezcla las dos. ANY-maze cita a **Barnes et al. 1997**; la guía cita a «Gallagher». Gallagher et al. 1993 definen la proximidad (*cumulative search error*) | Velocidad hasta la entrada. En el laberinto acuático, Gallagher mide la distancia al **centro** de la plataforma y aquí se mide al borde de la zona: los valores no son comparables con la literatura sin corregir |
| Corredor de Whishaw de una zona | Banda del ancho dado, centrada en la recta de la primera posición al centro de la zona (cápsula: la proyección se recorta al segmento) | dudoso | ANY-maze *Time spent in Whishaw's Corridor*: «centred on a line … from the animal's start position to the centre of the platform zone». No dice cómo se cierra por los extremos | Que la forma (rectángulo o cápsula) quede documentada |
| Cruces de líneas dentro de la zona | Centro dentro antes y después del cruce | correcto | Guía §9 | Igual |
| Grupos: distancia al borde | Transformada de distancia de la máscara | correcto | Guía §9 (contorno de la unión) | Igual |

### 1.5 Puntos (medidas propias de mANY-MAZE: ANY-maze no tiene *time near*, *approaches* ni *exploration* de puntos)

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Distancia media / mín. / máx., X/Y | Euclídea al punto | correcto | ANY-maze *Point measures* | Igual |
| *Time near*, *approaches*, latencia | Dentro del radio. doc: el **centro**. impl: la **cabeza** | dudoso | La guía no lo define (H-P06 n.º 7); ANY-maze no tiene esta medida | Que la guía diga qué parte del cuerpo se usa |
| Tiempo explorando, latencia | Cabeza ≤ radio **y** mirándolo (±45°). impl: además, cabeza a ≤ medio radio **aunque no lo mire** | doc correcta; **impl incorrecta** | Ennaceur & Delacour 1988; Leger et al. 2013; Antunes & Biala 2012: exploración = nariz dirigida al objeto a ≤ 2 cm. Subirse o sentarse encima **no** cuenta | La regla del medio radio cuenta como exploración estar encima del objeto o de espaldas pegado a él. Infla la exploración y diluye el índice de discriminación |
| Episodios de exploración | doc: cada tramo. impl: tramos de ≥ 0,2 s | dudoso | No está en la guía (H-P06 n.º 17) | Aceptable, pero debe estar documentado |
| Tiempo moviéndose hacia / lejos, distancia acercada / alejada, velocidad media hacia | Igual que en las zonas, desde la posición actual (doc) o la anterior (impl) | dudoso | Guía §9 dice «as ANY-maze: mobile». ANY-maze *Time moving towards the point* **no** exige movilidad | Lo mismo que en las zonas: el estado móvil deja pasar el temblor |
| Cabeza moviéndose hacia | Velocidad de la cabeza > umbral y ±45° | correcto respecto a la guía | Guía §9 | Igual |
| Cabeza orientada hacia / lejos, ángulo medio, veces que se orienta | Cabeza→punto, ≤ 30°; sin contar el inicio | correcto | ANY-maze *Time the animal's head was oriented…*, *Number of times…* | Igual (afectado por H-P01 en vídeo real) |

### 1.6 Líneas, rejillas, secuencias, teclas, rearing, zonas de investigación y ocultas, E/S, social

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Cruces de línea por sentido | Acepta cualquiera de los dos como «left-to-right» | dudoso | La guía no define el sentido | Que la guía diga qué es «izquierda→derecha» de una línea dibujada |
| Cruces de rejilla, celdas visitadas, celdas interiores | Cambios de celda del centro (sin contar los tramos fuera) | correcto respecto a la guía | Guía §3 *Grids* | El open field clásico cuenta un cruce cuando pasan las cuatro patas (Walsh & Cummins 1976). Por el centro salen más |
| Secuencias | Empezar en el primer paso; entrar en un paso fuera de orden es un error y termina el intento | correcto | Guía §3 *Sequences* | Igual |
| Teclas (y por zona) | Pulsaciones, duración, latencias; la pulsación cuenta en la zona del momento | correcto | Guía §9 *Keys* (as ANY-maze) | Igual |
| Rearing | Capa A: largo < 80 % del habitual ≥ 0,3 s (sin criterio de área ni de huecos de 0,2 s); el rear pertenece a la zona en que empieza | dudoso | Guía §9 *Rearing* (pide área < 75 % **y** largo < 80 %, unir huecos de 0,2 s y abrir un episodio al entrar en la zona levantado: el oráculo no codifica nada de esto). ANY-maze solo detecta rearing con vista **lateral** | Detectar el rearing desde arriba es una técnica propia, no validada en la literatura. H-P09. Validar con puntuación manual |
| Zona de investigación | Cabeza fuera, a ≤ la distancia del borde, y centro→cabeza a ≤ 45° de la dirección (desde el centro) al centro de la zona | correcto | Guía §3; ANY-maze *Number of investigation bouts* | Igual |
| Distancia investigando | Dos alternativas (el paso que llega o el que sale) | dudoso | No está en la guía | Elegir una y documentarla |
| Zona oculta: tiempo, salidas parciales | Escondido tras desaparecer en la zona o cerca (½ del tamaño) | correcto | Guía §3 *Hidden zone* | ANY-maze cuenta el tiempo escondido como **móvil** (T0358), y el oráculo no lo comprueba |
| E/S analógica: DE de la línea base | DE muestral (n−1) o poblacional (n) | dudoso (menor) | H-P06 n.º 16 | La DE muestral (n−1) es la convención en estadística aplicada |
| Social: contacto | Centros a ≤ un largo de cuerpo (dibujado o rastreado) | dudoso | La guía no lo fija | El contacto suele ser nariz–cuerpo a ≤ 2 cm. Por los centros, la distancia depende del tamaño del animal |

### 1.7 Medidas específicas de cada prueba

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Y: alternancias espontáneas, % | Tríos consecutivos con los tres brazos distintos; 100·SA/(entradas−2) | correcto | Hughes 2004; Lalonde 2002; Kraeuter et al. 2019 | Igual (con entradas por las cuatro patas, ver 1.4) |
| Y: vueltas al mismo brazo / al alterno | A-A / A-B-A | correcto | Lalonde 2002 | Igual |
| T: primera elección, latencia, alternancias | Primer brazo; cambios izquierda↔derecha entre entradas | correcto | Deacon & Rawlins 2006 (la alternancia clásica es por ensayo) | Igual |
| Radial: errores de memoria de trabajo, entradas correctas antes del primer error, entradas para visitarlos todos | Reentradas; índice del primer error; n.º de entradas | correcto | Olton & Samuelson 1976 | Igual |
| RAPC: errores de tipo 1 y 2 | Tipo 1 = reentrada en un brazo cebado ya visitado. Tipo 2 = toda entrada en uno no cebado, reentradas incluidas | dudoso | Guía §9 RAPC. Jarrard 1993: referencia = brazos no cebados; trabajo = reentradas. Otros clasifican la reentrada en un brazo no cebado como error de **trabajo** («working-incorrect») | Que la guía cite la convención. «Tipo 1/2» no es el nombre habitual: lo habitual es «trabajo/referencia» |
| RAPC: secuencia de puertas | Solo las puertas cruzadas al entrar en el brazo | dudoso | H-P06 n.º 10 | Documentarlo |
| Laberinto acuático: latencia de escape, plataforma encontrada, recorrido hasta ella | Primera entrada en la plataforma; distancia hasta ese cuadro | correcto | Vorhees & Williams 2006 | Igual (con el máximo del ensayo si no la encuentra) |
| Cruces de plataforma | Entradas; empezar encima no cuenta (una de las dos alternativas) | correcto | H-P06 n.º 15 | En la prueba de memoria el animal se suelta desde la pared, así que no cambia nada |
| % en el cuadrante objetivo / opuesto, % junto a la pared | Tiempo / duración | correcto | Vorhees & Williams 2006; Maei et al. 2009 | Igual |
| Distancia media / acumulada a la plataforma | 2 y 4 alternativas (toda la prueba o hasta encontrarla; por cuadro o cada 1 s) | dudoso (el oráculo no la fija) | Gallagher et al. 1993 (proximidad muestreada, al centro de la plataforma); Maei et al. 2009 | Una sola definición: la media de la distancia al centro de la plataforma durante el ensayo |
| Corredor de Whishaw (laberinto acuático): tiempo, %, distancia, % del recorrido, «ha salido del corredor» | Banda de 20 cm desde la primera posición al centro de la plataforma, hasta encontrarla. La distancia y el % del recorrido aceptan `into` (paso que entra) y `out` (paso que sale) | **`into` incorrecta**; `out` correcta | ANY-maze *Distance travelled in Whishaw's Corridor*: la distancia al entrar en el corredor «is NOT included» y la de salir «IS included». La guía dice lo mismo para el corredor de una zona. H-P06 n.º 13: la medida del laberinto acuático de la app cuenta el paso que entra | Una sola regla, la de ANY-maze |
| Barnes: latencia primaria, errores primarios, recorrido primario, errores totales, visitas al agujero de escape | Entradas de la nariz en los agujeros; errores = entradas en agujeros que no son el de escape (antes del primero / en total) | correcto | Sunyer et al. 2007 | Igual |
| EPM / EZM: % de tiempo en abiertos, % de entradas en abiertos, entradas | Abiertos/(abiertos+cerrados) (Pellow) o abiertos/duración; entradas en abiertos / total | correcto (las dos se publican; la app usa Pellow) | Pellow et al. 1985; Walf & Frye 2007 | Igual (con entradas por las cuatro patas) |
| NOR: exploración del objeto nuevo / familiar, índice de discriminación, índice de reconocimiento | Nariz ≤ radio y cuerpo mirando (±45°); (N−F)/(N+F); 100·N/(N+F) | correcto | Ennaceur & Delacour 1988; Antunes & Biala 2012; Leger et al. 2013 | Igual |
| Tres cámaras: interacción social, índice de sociabilidad | Centro en la zona de interacción de la taza (ANY-maze) **o** nariz y mirada | dudoso | Yang, Silverman & Crawley 2011 (tiempo en la cámara y tiempo olfateando la taza) | Olfateo = nariz a ≤ 2 cm mirando la taza. Estar en la zona por el centro sobreestima |
| CPP: puntuación, índice de preferencia, % en la cámara asociada | Asociada − no asociada; A/(A+B) o (A−B)/(A+B) | dudoso | Cunningham et al. 2006 (también se usa la diferencia post − pre test) | Que la guía diga la fórmula y si hace falta un pretest |
| Luz/oscuridad: latencia a la oscuridad, transiciones, % en la luz | Primera entrada; cambios de compartimento; luz/duración | correcto | Crawley & Goodwin 1980 | Igual |
| Tanque nuevo: latencia arriba, % arriba/abajo, cociente, profundidad media | Por zonas; profundidad = distancia al borde superior | correcto | Egan et al. 2009 | Igual |

### 1.8 Periodos e intervalos

| Medida | Definición del oráculo | Veredicto | Fuente | Qué esperaría un científico |
|---|---|---|---|---|
| Duración y distancia del periodo | (fin−inicio); `step[ia+1:ib]`, que pierde el paso del límite | correcto / **defecto menor del oráculo** | Guía §9 *Time periods*; ANY-maze *Test duration* por periodo | Que la suma de los intervalos dé el total de la prueba |
| Entradas en un periodo que empieza con el animal dentro | `w[0] + entradas` \| `entradas` (acepta las dos) | dudoso. Se recomienda la impl (no cuenta) | ANY-maze por periodo cuenta solo **transiciones** («no transition … occurred during the period», *Total time immobile*). Guía §9: una pulsación ya en curso al empezar el periodo «is time pressed, not a press» | La impl hace que las entradas de los intervalos sumen las de la prueba entera. El oráculo debería fijarla |
| Latencia a la primera entrada en ese periodo | 0 \| duración del periodo («nunca») | **dudoso (impl engañosa)** | H-P06 n.º 14. ANY-maze: «time from the start of the period to the first entry»; indefinida si no hay entrada | Un animal que pasa **todo** el intervalo en la zona aparece con la latencia máxima. Esperaría 0 o en blanco, nunca la duración |
| Inmovilidad, freezing, giros y rotaciones por intervalo | **No se comprueban** | — | ANY-maze: los episodios se recortan al periodo y solo cuentan las transiciones; la guía §9: rotaciones en el intervalo en que se completan | Hay que comprobarlo. Es donde suelen estar los errores |

---

## 2. Definiciones con más probabilidad de estar mal en el oráculo **y** en la app

H-P05 y H-P06 dicen que la app usa estas mismas definiciones, así que el oráculo no puede detectar estos errores.

1. **Acercarse / alejarse de una zona** (*time getting closer / further away*). No hay ningún umbral para el
   temblor. ANY-maze ignora «very small movements … based on the animal's size». Efecto en datos rastreados: +60–74 %
   (H-P05). Es la medida con el error más claro frente a ANY-maze.
2. **Inmovilidad por velocidad cuadro a cuadro (< 2 cm/s, ≥ 2 s).**
   - La guía no la define.
   - No es el criterio de ANY-maze, que mide el % del cuerpo quieto.
   - En la variante `doc`, un pico de un solo cuadro parte un episodio.
   - A baja resolución el temblor supera el umbral: −18 % de tiempo inmóvil en EPM (H-P05).

   Los laboratorios que comparen con ANY-maze o con puntuación manual lo notarán.
3. **Ángulo de giro, meander, velocidad angular, giro medio y rotaciones de la trayectoria.** Ningún paso tiene que
   superar una distancia mínima. El temblor que pasa el umbral de 2 cm/s las multiplica por ≈ 1,9, y las rotaciones
   de la trayectoria pasan de 0 a 5–6 (H-P05). Además, el ángulo de giro medio depende de la frecuencia de cuadros.
4. **Rotaciones del cuerpo.**
   - La regla de los 90° (guía) no es la de ANY-maze por defecto, que ignora las vueltas atrás parciales.
   - La variante que usa la app (H-P06 n.º 5) cuenta la vuelta siguiente desde el punto en que detecta la vuelta
     atrás. Eso contradice el ejemplo publicado de ANY-maze.

   Las dos cosas hacen que se cuenten menos vueltas en las pruebas de rotación.
5. **Corredor de Whishaw del laberinto acuático: distancia y % del recorrido.** La app cuenta el paso que **entra**
   (H-P06 n.º 13). Eso contradice a ANY-maze y a la regla de la propia guía para las zonas, y el oráculo lo acepta
   como alternativa.
6. **Moverse hacia / alejarse de zonas y puntos con el estado móvil** (H-P06 n.º 6). En las paradas de < 2 s, que
   cuentan como «móvil», el rumbo es el del temblor: añade tiempo «hacia» y «lejos» al azar.
7. **Exploración de un punto con la cabeza a ≤ medio radio aunque no lo mire** (H-P06 n.º 7). Cuenta como
   exploración subirse al objeto o pegarse a él de espaldas, algo que la literatura del NOR excluye
   expresamente. Infla la exploración y aplana el índice de discriminación.
8. **Latencia dentro de un periodo cuando el animal ya está en la zona** (H-P06 n.º 14). Sale «nunca» (la duración del
   periodo) aunque el animal pase en la zona el periodo entero.
9. **Error de rumbo inicial «tras 1 s móvil»** (H-P07). Con una parada de < 2 s al soltar al animal, el rumbo es el
   del temblor. El oráculo de la capa A lo tapa con un arreglo propio (el primer desplazamiento de > 0,5 cm) que la
   guía no contempla.
10. **Valores por defecto que no están en la guía:** latencia de lo que no ocurre = duración (ANY-maze: indefinida),
    freezing mínimo de 1 s (ANY-maze: 2 s), suavizado de 0,2 s y banda de tigmotaxis. El oráculo los tomó de la app.
    No son errores de cálculo, pero cambian los números que un laboratorio compararía con ANY-maze.

Para verificar (no se sabe si la app las calcula así; el oráculo acepta una alternativa incorrecta):

- **CIPL**: la variante `doc`, con la velocidad de toda la prueba y el recorrido hasta la entrada, no es ninguno
  de los dos modos de ANY-maze.
- ***Time facing***: la variante `doc`, medida desde el centro, contradice a ANY-maze, que mide desde la cabeza.
- **Transiciones entre zonas**: la variante `impl` no cuenta los tramos fuera de toda zona, a diferencia de las
  *line crossings* de ANY-maze.

No es de definición, pero invalida en vídeo real todas las medidas de la cabeza: **H-P01** (cabeza invertida en el
30–45 % de los cuadros en movimiento). El oráculo sintético no lo ve.

---

## 3. Recomendaciones (sin aplicar)

- Fijar en el oráculo una sola definición donde la guía o ANY-maze la fijan:
  - rotaciones: doc;
  - *time facing*: impl;
  - CIPL: impl;
  - Whishaw: `out`;
  - transiciones: doc;
  - distancia y distancia mínima al borde de la cabeza: doc.

  Con eso, la alternativa que hoy acepta deja de pasar como «ALTERNATIVA».
- Corregir en el oráculo las etiquetas «doc» que no son la lectura literal de la guía (sección 0, punto 2).
- Escribir los valores por defecto en la guía (§1 *Analysis*): umbral y tiempo de inmovilidad, freezing mínimo,
  suavizado, latencia de lo que no ocurre y banda de tigmotaxis. Hacerlo **antes** de seguir usándolos como verdad
  en el oráculo.
- Añadir al oráculo casos con **temblor de rastreo sintético en la capa A**, con el animal quieto. Hoy la verdad
  sintética no tiene temblor, así que la capa A nunca ve los errores 1–3 y 6, y la capa B los da por buenos.
- Comprobar por intervalo la inmovilidad, el freezing, los giros y las rotaciones, y que los intervalos sumen la
  prueba.
- Validar el rearing visto desde arriba y la inmovilidad contra puntuación manual en vídeo real de rata
  (B13).

---

## 4. Fuentes

**Guía:** `manymaze/resources/USER_GUIDE.md`: §1 *Protocol tab* (líneas 97–235), §3 *Zones*, *Grids*, *Sequences*
(290–340), §5 *How tracking works* (398–425), §9 *Measures* (1727–1933), *Time periods* (1984–2012).

**ANY-maze Help** (v7.62, `https://www.anymaze.co.uk/help/…`, consultado el 2026-10-11):

- [Immobility detection, T0358](https://www.anymaze.co.uk/help/any-maze_proto_tracking_immobility.htm)
- [Freezing detection, T0359](https://www.anymaze.co.uk/help/any-maze_proto_tracking_freezing.htm)
- [Rotations, T0654](https://www.anymaze.co.uk/help/any-maze_proto_analysis_rotations.htm)
- [Apparatus measures](https://www.anymaze.co.uk/help/any-maze_proto_appmsrs.htm)
- [Zone measures](https://www.anymaze.co.uk/help/any-maze_proto_zonemsrs.htm)
- [Point measures](https://www.anymaze.co.uk/help/any-maze_proto_pointmsrs.htm)
- [Analysis options](https://www.anymaze.co.uk/help/any-maze_proto_analysis.htm) (latencias, medias indefinidas,
  CIPL)
- [Movement towards/away](https://www.anymaze.co.uk/help/any-maze_proto_movement.htm)
- [Heading error options](https://www.anymaze.co.uk/help/any-maze_proto_analysis_headingerror.htm)
- [Zone entry settings](https://www.anymaze.co.uk/help/any-maze_proto_zonesettings.htm)
- [Analysis across time](https://www.anymaze.co.uk/help/any-maze_proto_analysis_acrosstime.htm)

**EthoVision XT 19:**

- [Meander](https://noldus.com/shared/resources/book/noldus-product-documentation/chapter/ethovision-xt/page/ethovision-xt-19-meander)
- [Turn angle](https://noldus.com/shared/resources/book/noldus-product-documentation/chapter/ethovision-xt/page/ethovision-xt-19-turn-angle)

**Literatura:**

- Anagnostaras SG et al. (2010) Front Behav Neurosci 4:158 (VideoFreeze; freezing ≥ 1 s).
- Antunes M, Biala G (2012) Cogn Process 13:93 (NOR, índices).
- Barnes CA et al. (1997) Nature 388:272 (CIPL).
- Benhamou S (2004) J Theor Biol 229:209 (rectitud y tortuosidad).
- Crawley J, Goodwin FK (1980) Pharmacol Biochem Behav 13:167 (luz/oscuridad).
- Cunningham CL, Gremel CM, Groblewski PA (2006) Nat Protoc 1:1662 (CPP).
- Deacon RMJ, Rawlins JNP (2006) Nat Protoc 1:7 (laberinto en T).
- Egan RJ et al. (2009) Behav Brain Res 205:38 (tanque nuevo).
- Ennaceur A, Delacour J (1988) Behav Brain Res 31:47 (NOR).
- Fanselow MS (1980) Pavlov J Biol Sci 15:177 (freezing).
- Gallagher M, Burwell R, Burchinal M (1993) Behav Neurosci 107:618 (proximidad / cumulative search error).
- Hughes RN (2004) Neurosci Biobehav Rev 28:497 (alternancia espontánea).
- Jarrard LE (1993) Behav Neural Biol 60:9 (errores de trabajo y de referencia en el radial).
- Kraeuter AK, Guest PC, Sarnyai Z (2019) Methods Mol Biol 1916:105 (laberinto en Y).
- Lalonde R (2002) Neurosci Biobehav Rev 26:91 (alternancia).
- Leger M et al. (2013) Nat Protoc 8:2531 (NOR: subirse al objeto no es explorar).
- Maei HR et al. (2009) Front Integr Neurosci 3:4 (medidas del laberinto acuático).
- Olton DS, Samuelson RJ (1976) J Exp Psychol Anim Behav Process 2:97 (radial).
- Pellow S et al. (1985) J Neurosci Methods 14:149 (EPM).
- Simon P, Dupuis R, Costentin J (1994) Behav Brain Res 61:59 (tigmotaxis).
- Sunyer B et al. (2007) Protocol Exchange, doi:10.1038/nprot.2007.390 (Barnes).
- Treit D, Fundytus M (1988) Pharmacol Biochem Behav 31:959 (tigmotaxis).
- Ungerstedt U, Arbuthnott GW (1970) Brain Res 24:485 (rotómetro).
- Vorhees CV, Williams MT (2006) Nat Protoc 1:848 (laberinto acuático).
- Walf AA, Frye CA (2007) Nat Protoc 2:322 (EPM).
- Walsh RN, Cummins RA (1976) Psychol Bull 83:482 (open field).
- Yang M, Silverman JL, Crawley JN (2011) Curr Protoc Neurosci 8.26 (tres cámaras).
