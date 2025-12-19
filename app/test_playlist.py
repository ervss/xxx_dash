import yt_dlp
import sys

# Sem vlož nejaký playlist na test (napr. Youtube alebo iný)
TEST_URL = "https://www.xvideos.com/favorite/88474735/esperanza_gomez"

def test_extraction():
    print(f"Testing playlist extraction for: {TEST_URL}")
    opts = {
        'extract_flat': True,
        'quiet': False, # Zapneme výpis, aby sme videli chyby
    }
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(TEST_URL, download=False)
            
            if 'entries' in info:
                print(f"\n[SUCCESS] Playlist detected: {info.get('title')}")
                print(f"Found {len(info['entries'])} videos.")
                for i, entry in enumerate(info['entries'][:3]):
                    print(f" - {i+1}. {entry.get('url') or entry.get('id')}")
                print("...")
            else:
                print("\n[FAIL] Not detected as playlist (no entries found).")
                
    except Exception as e:
        print(f"\n[ERROR] Extraction failed: {e}")

if __name__ == "__main__":
    test_extraction()