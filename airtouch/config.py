"""Parametri di configurazione, tutti raccolti in un unico posto."""

from dataclasses import dataclass


@dataclass
class Config:
    # --- webcam ---
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    # Senza richiesta esplicita molte webcam restano a 10 fps: chiederli
    # esplicitamente sblocca i 30 fps e rende il cursore molto piu' reattivo.
    target_fps: int = 30
    mirror: bool = True  # immagine a specchio: muovi a destra -> cursore a destra

    # --- rilevamento mano ---
    model_path: str | None = None  # None = modello di default in models/
    # Soglia alta: una falsa rilevazione farebbe schizzare il cursore.
    min_detection_confidence: float = 0.75
    min_tracking_confidence: float = 0.5

    # --- area attiva del frame mappata sullo schermo ---
    # Frazione del frame usata come "tavoletta": restringerla permette di
    # raggiungere i bordi dello schermo senza uscire dall'inquadratura.
    active_x_min: float = 0.22
    active_x_max: float = 0.78
    active_y_min: float = 0.18
    active_y_max: float = 0.72

    # --- filtro One Euro (smoothing del cursore) ---
    min_cutoff: float = 1.2   # piu' basso = piu' fluido ma piu' lento
    beta: float = 0.03        # piu' alto = meno latenza sui movimenti rapidi
    d_cutoff: float = 1.0

    # --- pinch (distanza normalizzata sulla dimensione della mano) ---
    pinch_on: float = 0.38    # sotto questa soglia il pinch e' chiuso
    pinch_off: float = 0.52   # sopra questa soglia il pinch e' aperto (isteresi)

    # --- click e drag ---
    click_cooldown: float = 0.30   # secondi tra due click consecutivi
    drag_hold: float = 0.45        # pinch tenuto oltre questo tempo -> drag

    # --- ancoraggio del cursore durante il pinch ---
    # Chiudendo le dita per cliccare, la punta dell'indice si sposta comunque:
    # quando le dita iniziano ad avvicinarsi il cursore passa a seguire un punto
    # del palmo, che nel pinch resta fermo. Il passaggio e' senza scatti perche'
    # lo scarto tra i due punti viene congelato e poi riassorbito.
    #
    # La soglia non e' assoluta ma relativa all'apertura abituale delle dita,
    # misurata di continuo: una soglia fissa, con una mano che tiene le dita
    # naturalmente raccolte, resterebbe agganciata per sempre.
    anchor_point: str = "palm_outer"  # palm_outer | palm_center | pinky_mcp | index_mcp | wrist
    anchor_window: float = 1.5     # secondi su cui si misura l'apertura abituale
    # Meglio agganciare presto: il passaggio non si vede, mentre la deriva
    # dell'indice accumulata prima dell'aggancio resta.
    anchor_ratio_on: float = 0.80  # chiuse sotto l'80% dell'apertura -> aggancio
    anchor_ratio_off: float = 0.92 # riaperte oltre il 92% -> si torna all'indice
    anchor_blend: float = 0.20     # secondi per riassorbire lo scarto al rilascio

    # --- gesti a pugno chiuso ---
    # Le distanze sono espresse in "mani" (multipli della dimensione della mano)
    # cosi' le soglie non dipendono da quanto sei lontano dalla webcam.
    swipe_window: float = 0.35        # finestra temporale per valutare uno scatto
    swipe_min_travel: float = 1.1     # spostamento minimo per uno swipe
    swipe_cooldown: float = 0.8       # pausa dopo uno swipe riconosciuto
    fist_still_travel: float = 0.20   # movimento oltre il quale il pugno non e' fermo
    fist_hold_seconds: float = 5.0    # quanto tenere il pugno fermo

    # --- gesto a due dita (indice + medio) ---
    axis_lock_travel: float = 0.025   # escursione prima di scegliere l'asse
    axis_deadzone: float = 0.004      # movimento per frame sotto il quale si ignora

    # --- varie ---
    show_preview: bool = True
    dry_run: bool = False  # riconosce i gesti ma non tocca il mouse reale
    grace_frames: int = 6  # frame senza mano tollerati prima di resettare lo stato
