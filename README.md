# AirTouch

Controlla mouse, volume e finestre di Windows muovendo la mano davanti alla webcam.

Stack: **OpenCV** (cattura) + **MediaPipe HandLandmarker** (21 punti della mano) +
**pynput** (mouse e tastiera reali).

Ogni gesto e' collegato alla sua azione in [gestures.json](gestures.json): il
riconoscimento e l'azione sono separati, quindi puoi rimappare tutto senza
toccare il codice.

## Avvio rapido

```
avvia.bat
```

Al primo avvio crea `.venv`, installa le dipendenze e scarica il modello
`hand_landmarker.task` (~8 MB) in `models/`.

Manualmente:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## Gesti riconosciuti

| Gesto | Nome nel JSON | Azione predefinita |
|---|---|---|
| Mano aperta che si muove | `point_move` | Muove il cursore |
| Pinch **pollice + indice**, tocco breve | `pinch_index_tap` | Click sinistro |
| Pinch **pollice + indice** tenuto > 0.45 s | `pinch_index_hold` | Drag |
| Pinch **pollice + medio**, tocco breve | `pinch_middle_tap` | Click destro |
| Pinch **pollice + anulare** | `pinch_ring_tap` / `_hold` | *(disponibile, non collegato)* |
| **Pugno** che scatta a sinistra | `fist_swipe_left` | `Ctrl+Win+â†` (desktop precedente) |
| **Pugno** che scatta a destra | `fist_swipe_right` | `Ctrl+Win+â†’` (desktop successivo) |
| **Pugno** che scatta in alto | `fist_swipe_up` | `Alt+Tab` |
| **Pugno** che scatta in basso | `fist_swipe_down` | `Alt+Shift+Tab` |
| **Pugno** chiuso e fermo per 5 s | `fist_hold` | Play / Pausa |
| **Indice + medio** aperti, mano su/giu' | `two_finger_vertical` | Scroll come la rotella |
| **Indice + medio** aperti, mano dx/sx | `two_finger_horizontal` | Volume su / giu' |

Note pratiche:

- Col pugno chiuso il cursore resta fermo: e' anche il modo per riposizionare
  la mano senza trascinare il puntatore.
- Un pinch non collegato a nessuna azione non produce eventi, ma viene misurato
  lo stesso: serve a distinguere i click fra loro (toccando l'anulare col
  pollice anche il medio finisce vicino, e senza confronto partirebbe il click
  sbagliato). Vince sempre il pinch effettivamente piu' stretto.
- Con indice e medio aperti l'**asse si blocca** alla prima escursione decisa:
  un movimento diagonale non fa scattare scroll e volume insieme.
- Dopo uno swipe c'e' una pausa di 0.8 s, per non incatenare `Alt+Tab` a raffica.
- La barra `PUGNO FERMO` nell'anteprima mostra l'avanzamento verso i 5 secondi.

## Come sta fermo il cursore mentre clicchi

Chiudendo il pollice sull'indice per cliccare, la punta dell'indice si sposta
sempre un po': se fosse lei a pilotare il cursore, ogni click partirebbe qualche
decina di pixel piu' in la'. Il programma allora **cambia punto di riferimento**:
appena le dita cominciano a chiudersi il cursore passa a seguire il bordo
esterno del palmo (lato mignolo, fra polso e base del mignolo), che durante un
pinch non si muove.

Due dettagli che lo rendono trasparente:

- **Nessuno scatto al cambio.** Lo scarto fra i due punti viene congelato
  nell'istante del passaggio e sommato al nuovo riferimento, quindi il cursore
  resta esattamente dov'era; al ritorno all'indice lo scarto si riassorbe in
  `anchor_blend` secondi.
- **Soglia relativa, non assoluta.** Il programma misura di continuo quanto
  sono distanti le dita a riposo (il massimo degli ultimi `anchor_window`
  secondi) e aggancia quando si chiudono sotto l'80% di quel valore. Con una
  soglia fissa, chi tiene le dita naturalmente raccolte resterebbe agganciato
  per sempre. Mentre un click o un drag e' in corso il riferimento si congela,
  altrimenti un trascinamento lungo se lo mangerebbe.

Nell'anteprima si vede tutto: un cerchio evidenzia il punto che sta pilotando il
cursore (verde = indice, rosso = palmo), l'intestazione dice `[indice]` o
`[palmo]`, e su ogni barra dei pinch la tacca grigia e' la soglia di aggancio
(che si sposta da sola) mentre quella rossa e' la soglia di click.

Con `anchor_point` si puo' scegliere un altro riferimento: `palm_outer`
(predefinito), `palm_center`, `pinky_mcp`, `index_mcp`, `wrist`.

## Configurare i gesti

Tutto sta in [gestures.json](gestures.json). Le righe che iniziano con `//` sono
commenti (estensione locale: JSON puro non li prevede).

```jsonc
{
  "settings": {
    "fist_hold_seconds": 5.0,
    "swipe_min_travel": 1.1,
    "drag_hold": 0.45
  },
  "bindings": {
    "point_move": "move_cursor",
    "pinch_index_tap": "left_click",
    "fist_swipe_up": "alt+tab",
    "fist_hold": "play_pause",
    "two_finger_vertical":   { "action": "scroll", "gain": 55 },
    "two_finger_horizontal": { "action": "volume", "gain": 14 }
  }
}
```

Elenco sempre aggiornato di gesti e azioni:

```
python main.py --list-gestures
```

### Azioni come stringa

| Stringa | Effetto |
|---|---|
| `move_cursor` | Muove il puntatore |
| `left_click`, `right_click`, `middle_click`, `double_click` | Click |
| `drag` | Tiene premuto il tasto sinistro |
| `scroll`, `scroll_h` | Rotella verticale / orizzontale |
| `volume` | Volume su/giu' proporzionale al movimento |
| `none` | Disattiva il gesto |
| qualsiasi combinazione di tasti | `"alt+tab"`, `"ctrl+win+left"`, `"win+d"`, `"play_pause"`, `"volume_up"`, `"mute"`, `"next_track"`, `"f5"` |

Modificatori validi: `ctrl`, `alt`, `shift`, `win`. Tasti speciali: frecce,
`tab`, `enter`, `esc`, `space`, `home`, `end`, `pageup`, `pagedown`, `delete`,
`f1`â€“`f20`, piu' i multimediali (`play_pause`, `stop`, `next_track`,
`prev_track`, `volume_up`, `volume_down`, `mute`).

### Azioni in forma estesa

| `action` | Parametri | Note |
|---|---|---|
| `hotkey` | `keys` | Come la stringa, ma esplicito |
| `scroll` | `gain`, `invert`, `horizontal` | `gain` = tacche per larghezza schermo |
| `volume` | `gain` | Gradini di volume per unita' di movimento |
| `click` | `button`, `count` | `count: 2` per il doppio click |
| `axis` | `positive`, `negative`, `gain`, `max_rate` | Movimento continuo -> pressioni ripetute |

`axis` e' il caso generale: `volume` non e' altro che questo, con `volume_up` e
`volume_down` ai due estremi. Per far scorrere i desktop virtuali muovendo due
dita in orizzontale:

```jsonc
"two_finger_horizontal": {
  "action": "axis",
  "positive": "ctrl+win+right",
  "negative": "ctrl+win+left",
  "gain": 6,
  "max_rate": 3
}
```

Se una voce e' sbagliata il programma non si pianta: stampa un avviso preciso
(gesto inesistente, tasto sconosciuto, azione incompatibile col tipo di gesto)
e prosegue con il resto della configurazione.

### Tipi di gesto

Ogni gesto ha un tipo, e accetta solo azioni compatibili:

| Tipo | Significato | Azioni ammesse |
|---|---|---|
| `cursor` | Posizione sullo schermo | `move_cursor` |
| `trigger` | Evento istantaneo | click, hotkey |
| `hold` | Stato acceso/spento | `drag` |
| `axis` | Movimento continuo | `scroll`, `volume`, `axis` |

## Comandi

| Tasto | Effetto |
|---|---|
| `Ctrl+Alt+Q` | Esce (globale, funziona anche senza finestra a fuoco) |
| `Ctrl+Alt+P` | Pausa / riprende il controllo (globale) |
| `q` o `Esc` | Esce (con la finestra di anteprima a fuoco) |
| `p` | Pausa |
| `h` | Mostra/nasconde lo scheletro della mano |

Le due scorciatoie globali sono la via di fuga: se il cursore impazzisce,
`Ctrl+Alt+P` restituisce subito il controllo alla mano vera.

## Opzioni da riga di comando

```
main.py [--config gestures.json] [--camera 0] [--width 640] [--height 480]
        [--fps 30] [--no-preview] [--no-mirror] [--dry-run]
        [--smoothing 1.2] [--sensitivity 1.0] [--model PERCORSO]
        [--list-gestures]
```

Le opzioni da riga di comando hanno la precedenza sulla sezione `settings`
del JSON.

- `--dry-run` â€” riconosce i gesti e mostra tutto, ma **non** tocca mouse e
  tastiera. Usalo per tarare le soglie in sicurezza.
- `--sensitivity` â€” > 1 riduce l'area attiva (basta muovere poco la mano),
  < 1 la allarga (piu' precisione, piu' movimento).
- `--smoothing` â€” frequenza di taglio del filtro: valori bassi (0.5) danno un
  cursore molto fluido ma piu' lento, valori alti (3) piu' reattivo ma nervoso.

## Anteprima

La finestra mostra lo scheletro della mano, il rettangolo dell'**area attiva**
(la porzione di inquadratura mappata sull'intero schermo), la modalita' corrente
con il riferimento in uso, gli fps, le barre `IND` / `MED` / `ANU` con la
distanza di ogni pinch (tacca rossa = soglia di click, tacca grigia = soglia di
aggancio al palmo) e la barra `PUGNO FERMO` mentre tieni il pugno chiuso.
Compaiono solo le barre dei pinch effettivamente collegati a un'azione.

## Struttura

| File | Ruolo |
|---|---|
| [main.py](main.py) | Riga di comando |
| [gestures.json](gestures.json) | Mappatura gesto -> azione |
| [airtouch/config.py](airtouch/config.py) | Soglie e parametri |
| [airtouch/detector.py](airtouch/detector.py) | MediaPipe Tasks + download del modello |
| [airtouch/hand.py](airtouch/hand.py) | Dai 21 landmark a dita estese / pinch |
| [airtouch/gestures.py](airtouch/gestures.py) | Riconoscimento: produce eventi con un nome |
| [airtouch/bindings.py](airtouch/bindings.py) | Lettura e validazione del JSON |
| [airtouch/actions.py](airtouch/actions.py) | Le azioni eseguibili (click, tasti, scroll) |
| [airtouch/engine.py](airtouch/engine.py) | Collega gli eventi alle azioni |
| [airtouch/controller.py](airtouch/controller.py) | Mouse reale |
| [airtouch/filters.py](airtouch/filters.py) | Filtro One Euro (anti-tremolio) |
| [airtouch/app.py](airtouch/app.py) | Ciclo webcam + anteprima |

## Taratura

Le soglie stanno in [airtouch/config.py](airtouch/config.py) e si possono
sovrascrivere dalla sezione `settings` del JSON:

- `pinch_on` / `pinch_off` â€” quanto vicine devono essere le dita per un click
  (doppia soglia: evita il tremolio a cavallo del limite).
- `drag_hold` â€” quanto tenere il pinch prima che diventi un drag.
- `anchor_point` / `anchor_ratio_on` / `anchor_ratio_off` / `anchor_window` /
  `anchor_blend` â€” l'aggancio del cursore al palmo durante il pinch (sopra).
  Metti `anchor_ratio_on` a 0 per disattivarlo e tornare a seguire solo l'indice.
- `swipe_min_travel` / `swipe_window` / `swipe_cooldown` â€” quanto ampio e veloce
  dev'essere lo scatto del pugno. Le distanze sono in "mani", cioe' multipli
  della dimensione della mano inquadrata: la soglia non cambia se ti avvicini
  o ti allontani dalla webcam.
- `fist_still_travel` / `fist_hold_seconds` â€” quanto fermo e per quanto tempo.
- `axis_lock_travel` / `axis_deadzone` â€” quando l'asse delle due dita si decide
  e sotto quale movimento si ignora.
- `active_x_*` / `active_y_*` â€” la porzione di frame usata come tavoletta.
- `min_cutoff` / `beta` â€” filtro One Euro: `min_cutoff` basso = piu' fluido,
  `beta` alto = meno latenza sui movimenti rapidi.

## Note

- Serve luce decente: al buio la webcam abbassa il frame rate e il tracking
  diventa scattoso. Il programma chiede esplicitamente 30 fps alla webcam,
  che di default su molti modelli restano 10.
- Una sola mano alla volta (`num_hands=1`), la piu' evidente nell'inquadratura.
- L'immagine e' a specchio: muovi la mano a destra, il cursore va a destra.
  Con `--no-mirror` si inverte.

## Come e' fatto

Il lavoro pesante â€” trovare la mano e i suoi 21 punti in ogni fotogramma â€” lo fa
il modello **HandLandmarker** di MediaPipe, che gira in locale sulla CPU. Tutto
il resto e' codice di questo repository: interpretare quei punti come dita
aperte, pinch e movimenti, e tradurli in comandi per Windows.

La catena, fotogramma per fotogramma:

1. **OpenCV** legge il frame dalla webcam (DirectShow) e lo specchia.
2. **MediaPipe HandLandmarker** (Tasks API, modalita' VIDEO) restituisce i 21
   landmark normalizzati della mano.
3. [hand.py](airtouch/hand.py) li riduce a grandezze indipendenti da distanza e
   rotazione: dita estese (confronto di distanze dal polso, non della sola
   coordinata verticale), distanze di pinch divise per la dimensione della mano,
   punti di riferimento del palmo.
4. [gestures.py](airtouch/gestures.py) e' la macchina a stati: isteresi sui
   pinch, finestra temporale per gli swipe, blocco d'asse per le due dita,
   aggancio adattivo del cursore.
5. Il **filtro One Euro** ([filters.py](airtouch/filters.py)) smorza il
   tremolio senza aggiungere latenza sui movimenti veloci.
6. [actions.py](airtouch/actions.py) esegue: **pynput** per cursore, click,
   rotella e per le combinazioni di tasti, compresi i tasti multimediali.

Il codice e' stato scritto con **Claude Code** (Anthropic), in un percorso a piu'
passaggi: prima il controllo del mouse, poi la configurazione via JSON, poi la
stabilizzazione del cursore durante i click. Le scelte non banali (soglie in
"mani" invece che in pixel, aggancio relativo invece che assoluto, disambiguazione
dei pinch a favore del piu' stretto) sono nate da problemi concreti emersi
provando, e sono annotate nei commenti dove contano.

## Credits e licenze

Questo progetto e' un assemblaggio di componenti di terzi. Il merito del
riconoscimento della mano e' interamente di MediaPipe.

| Componente | Autore | Licenza | Ruolo |
|---|---|---|---|
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | Google | Apache 2.0 | Rilevamento della mano e dei 21 landmark |
| Modello `hand_landmarker.task` | Google | Apache 2.0 | Pesi del rilevatore, scaricati da `storage.googleapis.com/mediapipe-models` |
| [OpenCV](https://opencv.org/) (`opencv-python`) | OpenCV team | Apache 2.0 | Cattura webcam e finestra di anteprima |
| [pynput](https://github.com/moses-palmer/pynput) | Moses PalmÃ©r | LGPL v3 | Controllo di mouse e tastiera, hotkey globali |
| [NumPy](https://numpy.org/) | NumPy developers | BSD 3-Clause | Base numerica (dipendenza di OpenCV e MediaPipe) |

L'algoritmo di smoothing e' il **1â‚¬ (One Euro) Filter**, reimplementato qui a
partire dalla descrizione dell'articolo originale:

> GÃ©ry Casiez, Nicolas Roussel, Daniel Vogel.
> *1â‚¬ Filter: A Simple Speed-based Low-pass Filter for Noisy Input in
> Interactive Systems.* CHI 2012, pp. 2527â€“2530.
> <https://gery.casiez.net/1euro/>

Il modello di MediaPipe viene scaricato a runtime e **non** e' incluso nel
repository (vedi [.gitignore](.gitignore)); vale la licenza Apache 2.0 di Google.
Attenzione alla licenza di pynput: e' **LGPL v3**, quindi piu' vincolante delle
altre se un giorno questo codice venisse ridistribuito.

Codice scritto con l'assistenza di **Claude Code** (Anthropic).
