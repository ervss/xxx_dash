# Quantum VIP Dashboard v3

Vitajte v tretej generácii Quantum VIP Dashboard – ultimátneho nástroja na správu, analýzu a prehliadanie vašej osobnej video knižnice.

## ✨ Funkcionalita

*   **Pokročilý Import:** Importujte videá z textových súborov, JSON súborov alebo jednoduchým vložením zoznamu URL adries.
*   **Automatické Metadáta:** Pomocou `yt-dlp` sa automaticky sťahujú metadáta ako názov, dĺžka, kvalita a tagy.
*   **Generovanie Náhľadov:** Aplikácia automaticky generuje statické náhľady a **animované GIF náhľady** pre rýchle prezretie obsahu.
*   **AI Tagovanie:** Vstavaná umelá inteligencia (`spaCy`) analyzuje názvy a popisy videí a automaticky navrhuje relevantné tagy (osoby, miesta, produkty).
*   **Full-text Vyhľadávanie v Titulkoch:** Unikátna funkcia "Super Search" (`Ctrl+K`) umožňuje prehľadávať obsah stiahnutých titulkov a nájsť tak presný moment vo videu.
*   **Stránka so Štatistikami:** Prehľadné grafy a štatistiky o vašej knižnici dostupné na dedikovanej stránke `/stats`.
*   **Moderný Prehrávač:** Prehrávač s podporou rozdelenej obrazovky (Split Screen), vizuálnymi filtrami a ukladaním pozície.
*   **Hromadné Operácie:** Jednoducho označujte, mažte alebo pridávajte videá medzi obľúbené v dávkovom režime.

## 🚀 Spustenie

### Požiadavky
*   Python 3.8+
*   `pip` (manažér balíčkov pre Python)

### Inštalácia

1.  **Stiahnite si `ffmpeg`:**
    Aplikácia vyžaduje `ffmpeg` na spracovanie videí. Stiahnite si ho z [oficiálnej stránky ffmpeg.org](https://ffmpeg.org/download.html).
    Po stiahnutí rozbaľte archív a umiestnite súbory `ffmpeg.exe` a `ffprobe.exe` do hlavného (koreňového) priečinka tohto projektu.

2.  **Nainštalujte Python závislosti:**
    V termináli otvorte priečinok projektu a spustite nasledujúci príkaz:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Stiahnite AI model:**
    Pre fungovanie AI tagovania je potrebný jazykový model. Stiahnite ho príkazom:
    ```bash
    python -m spacy download en_core_web_sm
    ```

### Spustenie Aplikácie

1.  **Spustite vývojový server:**
    V termináli spustite Uvicorn server:
    ```bash
    uvicorn app.main:app --reload
    ```
    Flag `--reload` zabezpečí, že sa server automaticky reštartuje pri každej zmene v kóde.

2.  **Otvorte aplikáciu v prehliadači:**
    Otvorte nasledujúcu adresu: [http://127.0.0.1:8000](http://127.0.0.1:8000)

## ⌨️ Klávesové Skratky

| Skratka       | Akcia                                               |
|---------------|-----------------------------------------------------|
| `Ctrl` + `K`  | Otvorí "Super Search" (vyhľadávanie v titulkoch)    |
| `Esc`         | Zatvorí akékoľvek modálne okno, prehrávač alebo dávkový režim |
| `Medzerník`   | Pozastaví/spustí video (keď je otvorený prehrávač)   |
| `S`           | Zapne/vypne režim rozdelenej obrazovky (Split Screen) |
| `F`           | Zapne/vypne režim celej obrazovky (Fullscreen)      |

---
_Vytvorené s pomocou AI asistenta._
