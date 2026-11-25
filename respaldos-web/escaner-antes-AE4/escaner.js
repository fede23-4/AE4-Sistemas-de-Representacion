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
   ═══════════════════════════════════════════════════════════════════════════ */
window.ESCANER = {
  modelo: 'AQ-1',

  imagenes: {
    iso: 'img/aq1-iso-blanco.webp',             // isométrica en transparencia (líneas blancas)
    isoLuz: 'img/aq1-iso-turquesa.webp',        // la misma, en turquesa con brillo (haz de escaneo)
    despiece: 'img/despiece-blanco.webp',       // isométrica explotada (líneas blancas)
    despieceLuz: 'img/despiece-turquesa.webp'   // la misma, en turquesa con brillo (pieza iluminada)
  },

  /* Etiquetas sobre la isométrica en la portada de la página. lado: 'der' o 'izq'. */
  portada: [
    {x: 77, y: 51, lado: 'der', titulo: 'Módulo NIR', detalle: '900–1700 nm'},
    {x: 12, y: 41, lado: 'izq', titulo: '3 pulsadores', detalle: 'IP67 · IK10'},
    {x: 16, y: 78, lado: 'izq', titulo: 'LEDs de estado', detalle: 'carga · escaneo · enlace', ocultarEnPantallaChica: true}
  ],

  /* Llamadas numeradas sobre la isométrica (diapositiva "Formato pistola"). */
  formato: [
    {x: 77, y: 51, titulo: 'Sensor en el frente', texto: 'El cabezal apunta a la muestra, la mano no tapa nada'},
    {x: 31, y: 89, titulo: 'Resultados por Bluetooth / Wi-Fi', texto: 'El cabezal no admite pantalla sin perder ergonomía'},
    {x: 16, y: 78, titulo: 'Tres LEDs en el mango', texto: 'Batería, escaneo y pantalla conectada'},
    {x: 12, y: 41, titulo: 'Sellado', texto: 'Pulsadores IP67 / IK10 y pocas piezas móviles'}
  ],

  /* Piezas de la isométrica explotada, en el orden de la lista de despiece del plano. */
  piezas: [
    {cant: 3, nombre: 'Pulsador antivandálico', lista: 'Pulsadores antivandálicos', modelo: 'Metzler IP67 / IK10', x: 24.8, y: 40, r: 13,
     texto: 'Conmutadores electromecánicos para entornos industriales: sellados contra polvo y agua (IP67) y resistentes a impactos (IK10).'},
    {cant: 1, nombre: 'Circuito de carga y protección', modelo: 'TP4056 + DW01A + FS8205A', x: 39.5, y: 40.6, r: 10,
     texto: 'Carga la batería de litio por USB-C y la protege contra sobrecarga, sobredescarga y cortocircuito.'},
    {cant: 1, nombre: 'Placa de control', modelo: 'ESP32-S3 DevKitC-1', x: 42, y: 6.8, r: 15,
     texto: 'Doble núcleo de 32 bits con aceleración vectorial para IA, Wi-Fi y Bluetooth 5 (LE). Procesa el espectro, guarda las mediciones y controla la interfaz.'},
    {cant: 1, nombre: 'Módulo óptico NIR', modelo: 'Pynect NIR-M-R2', x: 57.5, y: 20, r: 18,
     texto: 'Emite radiación de infrarrojo cercano hacia la muestra y captura la respuesta espectral reflejada.'},
    {cant: 1, nombre: 'Convertidor DC-DC', modelo: 'LM2596', x: 62, y: 32, r: 12,
     texto: 'Regulador reductor de alta eficiencia: lleva la tensión de la batería a un nivel estable para la lógica, la óptica y el procesamiento.'},
    {cant: 1, nombre: 'Carcasa del cabezal', modelo: 'Diseño propio', x: 52, y: 56, r: 36,
     texto: 'Forma prismática con esquinas y aristas redondeadas que aloja y protege la electrónica del cabezal.'},
    {cant: 1, nombre: 'Ventana protectora óptica', modelo: 'Diseño propio', x: 81.5, y: 64, r: 9,
     texto: 'Protege el módulo NIR en el frente del cabezal, por donde el sensor mira a la muestra.'},
    {cant: 1, nombre: 'Mango / Empuñadura', lista: 'Mango y empuñadura', modelo: 'Diseño propio', x: 40.5, y: 86, r: 17,
     texto: 'Agarre para una mano con textura antideslizante. Lleva los tres LEDs de estado y, en la base, el puerto USB-C de carga.'}
  ],

  /* Pieza que se muestra primero en la anatomía (índice en la lista de piezas). */
  piezaInicial: 3
};
