/* ═══════════════════════════════════════════════════════════════════════════
   DATOS DEL ESCÁNER (AQ-1)
   Todo lo que depende del diseño del equipo está acá. La página y la presentación
   arman las animaciones, la anatomía, la tabla de despiece y las llamadas a partir
   de estos datos, así que cuando cambie el diseño alcanza con:

   1. Exportar los planos nuevos (isométrica e isométrica explotada) y correr
        python escaner/generar.py --iso <isometrica.png> --despiece <explotada.png>
      Eso regenera los dibujos de líneas en img/ y las facetas de la vista general
      (escaner/facetas.js).
   2. Actualizar abajo los componentes, las etiquetas y sus posiciones.
      Para medir posiciones, abrí ApexQuant.html#calibrar: al pasar el mouse sobre
      cualquier dibujo del escáner se muestra la coordenada x/y en %, y al hacer clic
      se copia.

   Coordenadas: x e y en % del ancho y alto de la imagen correspondiente.
   r: radio de la luz que ilumina la pieza, en % del ancho del dibujo.

   Versión AE4 (diseño final): láminas A3 "02 Multivistas", "04 Isométrica" y
   "04 Isométrica explotada" (15 piezas, mismo orden que la lista de piezas del plano).
   ═══════════════════════════════════════════════════════════════════════════ */
window.ESCANER = {
  modelo: 'AQ-1',

  imagenes: {
    iso: 'img/aq1-iso-blanco.webp',             // isométrica (líneas blancas)
    isoLuz: 'img/aq1-iso-turquesa.webp',        // la misma, en turquesa con brillo (haz de escaneo)
    despiece: 'img/despiece-blanco.webp',       // isométrica explotada (líneas blancas)
    despieceLuz: 'img/despiece-turquesa.webp'   // la misma, en turquesa con brillo (pieza iluminada)
  },

  /* Etiquetas sobre la isométrica en la portada de la página. lado: 'der' o 'izq'. */
  portada: [
    {x: 15, y: 55, lado: 'izq', titulo: 'Módulo NIR', detalle: '900–1700 nm · ventana de zafiro'},
    {x: 66, y: 40, lado: 'der', titulo: 'Rejillas de ventilación', detalle: 'bisagras e imanes de neodimio'},
    {x: 63, y: 52, lado: 'izq', titulo: 'Mango con 4 celdas 21700', detalle: '4S1P · 14,8 V · 4,9 Ah', ocultarEnPantallaChica: true}
  ],

  /* Llamadas numeradas sobre la isométrica (diapositiva "Formato pistola"). */
  formato: [
    {x: 15, y: 55, titulo: 'Sensor en el frente', texto: 'El cabezal apunta a la muestra y la mano no tapa nada'},
    {x: 66, y: 40, titulo: 'Piezas laterales con rejilla', texto: 'Ventilan y dan acceso al módulo de energía'},
    {x: 79, y: 84, titulo: 'Mango con las baterías', texto: 'Gatillo empotrado, LED bicolor y USB-C en la base'},
    {x: 91, y: 30, titulo: 'Sin pantalla, sin cables', texto: 'Resultados a la PC por Bluetooth o Wi-Fi'}
  ],

  /* Piezas de la isométrica explotada, en el orden de la lista de piezas del plano. */
  piezas: [
    {cant: 1, nombre: 'Carcasa principal', modelo: 'ABS', x: 45, y: 63, r: 22,
     texto: 'Caja abierta hacia arriba de 80 × 222 × 96 mm con paredes de 3 mm. Aloja la electrónica y lleva la ventana óptica de zafiro en el frente.'},
    {cant: 1, nombre: 'Tapa superior', modelo: 'ABS', x: 50, y: 22, r: 22,
     texto: 'Cierra la carcasa por arriba y se atornilla con cuatro tornillos en las esquinas.'},
    {cant: 1, nombre: 'Brida del mango', modelo: 'ABS', x: 58, y: 72, r: 9,
     texto: 'Fija el mango por debajo de la carcasa con dos tornillos.'},
    {cant: 1, nombre: 'Mango', lista: 'Mango / empuñadura', modelo: 'ABS', x: 59, y: 88, r: 14,
     texto: 'Empuñadura ahusada tipo pistola para una mano. Aloja las cuatro baterías Samsung 21700, el pulsador empotrado, el LED bicolor y, en la base, el USB-C con su obturador.'},
    {cant: 1, nombre: 'Carcasa lateral derecha', modelo: 'ABS', x: 64, y: 66, r: 11,
     texto: 'Pieza con rejilla de ventilación. Abre con bisagras y cierra con dos imanes de neodimio para acceder al módulo de energía.'},
    {cant: 1, nombre: 'Carcasa lateral izquierda', modelo: 'ABS', x: 32, y: 60, r: 8,
     texto: 'Igual a la derecha, del otro lado: rejilla de ventilación, bisagras e imanes, con un desnivel en la cara inferior para abrirla a mano.'},
    {cant: 1, nombre: 'Gabinete del módulo de gestión de carga', lista: 'Gabinete de gestión de carga', modelo: 'Aluminio 1100-H14 + ABS', x: 52, y: 48, r: 13,
     texto: 'Gabinete de dos pisos: abajo el BMS que protege y balancea las celdas, arriba el cargador, separados por una placa de FR4. La base y dos paredes de aluminio disipan el calor hacia las rejillas.'},
    {cant: 1, nombre: 'Pulsadores antivandálicos', modelo: 'Metzler IP67 / IK10', x: 69, y: 56, r: 7,
     texto: 'En la parte trasera del cabezal, en disposición piramidal: encendido y apagado, conectividad Wi-Fi / Bluetooth e inicio y fin del modo promedio.'},
    {cant: 1, nombre: 'Pulsador empotrado', lista: 'Gatillo del mango', modelo: 'Elaboración propia · ABS', x: 55, y: 76, r: 6,
     texto: 'Gatillo del mango: cada pulsación inicia una medición NIR sin cambiar el agarre. En modo promedio suma una lectura más al conjunto.'},
    {cant: 1, nombre: 'Tornillería del módulo de gestión', lista: 'Tornillería del módulo', modelo: 'Nylon 6/6', x: 44, y: 35, r: 7,
     texto: 'Tornillos y tuercas de nylon: livianos y aislantes, sin riesgo de cortocircuito. Se aseguran con LOCTITE 425.'},
    {cant: 1, nombre: 'Tornillos de la tapa superior', lista: 'Tornillos de la tapa', modelo: 'Nylon 6/6', x: 26, y: 20, r: 6,
     texto: 'Los cuatro tornillos de las esquinas que fijan la tapa superior a la carcasa.'},
    {cant: 1, nombre: 'Bisagras', modelo: 'Acero inoxidable AISI 304', x: 37, y: 33, r: 5,
     texto: 'Articulan las dos piezas laterales con rejilla. El acero inoxidable resiste la corrosión en los ciclos de apertura para mantenimiento.'},
    {cant: 1, nombre: 'Tornillos del módulo NIR', modelo: 'Nylon 6/6', x: 24, y: 52, r: 5,
     texto: 'Fijan el módulo espectral en posición vertical, orientado hacia la ventana óptica.'},
    {cant: 1, nombre: 'Módulo espectral NIR', lista: 'Módulo NIR', modelo: 'Pynect NIR-M-R2', x: 33, y: 52, r: 9,
     texto: 'Emite y registra la radiación entre 900 y 1700 nm a través de una ventana de zafiro. Se comunica con el ESP32-S3 por UART.'},
    {cant: 1, nombre: 'Cargador USB-PD', modelo: 'USB-PD-CHG-EVM-01', x: 48, y: 40, r: 10,
     texto: 'Piso superior del módulo: carga las baterías desde el USB-C por USB Power Delivery y entrega 5 V regulados al ESP32 y al NIR, también mientras carga.'}
  ],

  /* Pieza que se muestra primero en la anatomía (índice en la lista de piezas). */
  piezaInicial: 13
};
