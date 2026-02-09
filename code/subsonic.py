import requests
import time
import utility
import os
from thefuzz import fuzz
import re

def subsonic_error_from_json(data):
    """Checks if the Subsonic response contains a failed status and returns the error code/message."""
    try:
        sr = data.get("subsonic-response", {})
        if sr.get("status") == "failed":
            err = sr.get("error", {}) or {}
            code = err.get("code")
            msg = err.get("message", "Unknown Subsonic error")
            return code, msg
    except Exception:
        pass
    return None

def subsonic_get_json(url, params, tries=3, timeout=30):
    """Performs a GET request to the Subsonic API with retry logic and JSON validation."""
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            # JSON decode
            data = r.json()
            # Check Subsonic "status"
            err = subsonic_error_from_json(data)
            if err:
                code, msg = err
                print(f"[Subsonic FAILED] {url} code={code} message={msg}")
                return None
            return data
        except requests.exceptions.RequestException as e:
            last_exc = e
            wait = 2 ** (attempt - 1)
            print(f"[Network error] {url} attempt {attempt}/{tries}: {e} (retry in {wait}s)")
            time.sleep(wait)

        except ValueError as e:
            # JSON invalide
            print(f"[JSON decode error] {url}: {e}")
            return None
    print(f"[Giving up] {url}: {last_exc}")
    return None

def perform_requests(url, params):
    return subsonic_get_json(url, params, tries=3, timeout=30)
    
def parse_search(data, target_artist, target_title):
    """
    Parses Subsonic search results and calculates similarity scores.
    Optimizes results by cleaning titles and checking for artist inclusions.
    """
    if not data or 'subsonic-response' not in data or 'searchResult3' not in data['subsonic-response']:
            return []
        
    search_res = data['subsonic-response']['searchResult3']
    if 'song' not in search_res:
            print(f"   [DEBUG] No songs found in this search batch.")
            return []
            
    tracks = search_res['song']
    tracks_dict = []
    
    print(f"   [DEBUG] Found {len(tracks)} candidates. processing...")

    for track in tracks:
        track_artist = track['artist']
        track_title = track['title']
        
        # 1. Calcul du score brut (sans nettoyage du titre)
        similarity_note = utility.similarity(target_artist, target_title, track_artist, track_title)
        raw_score = similarity_note 
        
        # 2. OPTIMISATION : On nettoie le titre trouvé (enlève (feat. xxx))
        # Grâce à la modif dans utility.py, cela va transformer "XX FILES (feat. Alpha Wann)" en "XX FILES"
        clean_track_title = utility.clean_artist_name(track_title)
        
        score_boosted = False
        # Si le nettoyage a changé quelque chose (ex: enlevé le feat), on recalcule
        if clean_track_title != track_title:
             similarity_note_optimized = utility.similarity(target_artist, target_title, track_artist, clean_track_title)
             # On garde le meilleur score
             if similarity_note_optimized > similarity_note:
                 similarity_note = similarity_note_optimized
                 score_boosted = True

        # 3. Boost si l'artiste cible est inclus dans l'artiste trouvé (ex: "Jungle Jack" dans "Jungle Jack & Alpha Wann")
        clean_target_artist = utility.clean_artist_name(target_artist).lower()
        clean_track_artist = utility.clean_artist_name(track_artist).lower()
        
        artist_included = False
        if clean_target_artist in clean_track_artist and similarity_note > 0.60:
             similarity_note = max(similarity_note, 0.85)
             artist_included = True

        # --- DEBUG LOGS ---
        if similarity_note > 0.1:
            print(f"      [Candidate] {track_artist} - {track_title}")
            print(f"          Target: {target_artist} - {target_title}")
            print(f"          Raw Score: {raw_score:.2f}")
            if score_boosted:
                print(f"          Cleaned Title Attempt: '{clean_track_title}'")
                print(f"          New Score: {similarity_note:.2f}")
            print(f"          -> Final Decision: {'KEEP' if similarity_note >= 0.80 else 'REJECT'}")
            print("-" * 10)
        # ------------------

        if similarity_note < 0.80:
            continue
            
        track_info = {
            "title": track_title,
            "artist": track_artist,
            "similarity": similarity_note,
            "download_id": track['id'],
            "isexternal": track['isExternal']
        }
        tracks_dict.append(track_info)
    return tracks_dict

def search_octo(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS, artist, title):
    """Searches the Subsonic server (and Octo-Fiesta) using multiple query variations."""
    url = SUBSONIC_URL+"/rest/search3"
    query = f"{artist} {title}"
    base_params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'f': 'json',
        'query': query,
        'songCount': 20,
        'artistCount': 0,
        'albumCount': 0
    }

    cleaned_artist = utility.clean_artist_name(artist)
    cleaned_title = utility.clean_title(title)
    search_queries = [
        f"{artist} {title}",
        f"{cleaned_artist} {cleaned_title}",
        f"{cleaned_artist} {title}",
        f"{artist} {cleaned_title}"
    ]
    all_tracks_found = []
    for query in search_queries:
        # if perfect local match already exist : stop
        if any(t['isexternal'] is False and t['similarity'] > 0.9 for t in all_tracks_found):
            break
        params = base_params.copy()
        params['query'] = query
        # get all the 50 search result 
        data = perform_requests(url, params)
        # get the similarity between request and found tracks, if < 80 don't keep it
        results = parse_search(data, artist, title)
        # results is from each similare tracks : track title, artist, similarity note, download_id and isexternal value
        all_tracks_found.extend(results)
    unique_tracks = {t['download_id']: t for t in all_tracks_found}.values() # get only unique ID of tracks founds from octo-fiesta
    return list(unique_tracks)
    
def compare_tracks(tracks_dict):
    """Prioritizes local tracks over external ones, then picks the highest similarity."""
    if tracks_dict:
        result = max(tracks_dict, key=lambda x: (not x['isexternal'], x['similarity'])) 
        return result
    return None

def download_tracks(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS, id):
    """Triggers a download/stream on the Subsonic server (used for Octo-Fiesta integration)."""
    url = SUBSONIC_URL+"/rest/stream"
    params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'id': id,
        'maxBitRate': 1 # Optionnel : demande du mp3 pour aller plus vite, Octo téléchargera quand même le max dispo
    }
    try:
        # stream=True est CRUCIAL ici
        with requests.get(url, params=params, stream=True, timeout=10) as r:
            r.raise_for_status()
            for _ in r.iter_content(chunk_size=1024):
                break 
        print(f"Download trigger successful for ID: {id} (Trigger only)")
    except Exception as e:
        print(f"Error triggering download: {e}")
        return None
    
def start_scan(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS):
    """Triggers a library scan and waits for it to complete."""
    scan_url = SUBSONIC_URL+"/rest/startScan"
    status_url = SUBSONIC_URL + "/rest/getScanStatus"
    params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'f': 'json'
    }

    if not subsonic_get_json(scan_url, params, tries=3, timeout=30):
        print("startScan failed.")
        return None
    print("Scan command sent")
    time.sleep(2)
    consecutive_fail = 0
    while True:
        data = subsonic_get_json(status_url, params, tries=1, timeout=30)
        if not data:
            consecutive_fail +=1
            if consecutive_fail >= 10:
                print("Too many failures reading scan status. Aborting scan wait.")
                return None
            time.sleep(2)
            continue

        consecutive_fail = 0
        scan_status = data['subsonic-response'].get('scanStatus', {})
        is_scanning = scan_status.get('scanning')
        count = scan_status.get('count', 0)
        if is_scanning is False:
            print(f"\nScan finished. Total items scanned: {count}")
            break

        print(f"Scanning in progress... ({count} items)", end="\r")
        time.sleep(2)

    print("\nScan ended.")
    return True


def create_playlist(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS, playlist_name, songs_id):
    url = SUBSONIC_URL+"/rest/createPlaylist"
    params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'f': 'json',
        'name': playlist_name,
        'songId': songs_id
    }
    data = subsonic_get_json(url, params, tries=3, timeout=30)
    if not data:
        print(f"Playlist NOT created: '{playlist_name}'")
        return None
    print(f"Playlist '{playlist_name}' created with {len(songs_id)} titles")
    return data

def get_all_playlists(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS):
    url = SUBSONIC_URL + "/rest/getPlaylists"
    params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'f': 'json'
    }
    data = subsonic_get_json(url, params)
    if not data:
        return []
    try:
        resp = data.get("subsonic-response", {})
        playlists_container = resp.get("playlists", {})
        playlists = playlists_container.get("playlist", [])
        return playlists
    except Exception as e:
        print(f"Error parsing playlists: {e}")
        return []

def get_playlists_songs(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS):
    all_songs_ids = []
    url = SUBSONIC_URL + "/rest/getPlaylist"
    playlists = get_all_playlists(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS)
    for playlist in playlists:
        playlist_name = playlist.get('name', '')
        if "Weekly Discovery" in playlist_name: 
            print(f"[TEST] Playlist ignorée pour la protection : {playlist_name}")
            continue
        id = playlist.get('id')
        params = {
            'u': SUBSONIC_USER,
            'p': SUBSONIC_PASS,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'id': id
        }
        data = subsonic_get_json(url, params)
        if not data:
            continue
        try:
            resp = data.get("subsonic-response", {})
            json_playlist = resp.get("playlist", {})
            entry = json_playlist.get('entry', [])
            if not entry:
                return []
            for ent in entry:
                song_id = ent.get('id')
                if song_id:
                    all_songs_ids.append(song_id)
        except Exception as e:
            print(f"Error parsing playlists: {e}")
            continue
    all_songs_ids = list(dict.fromkeys(all_songs_ids))
    return all_songs_ids

def get_liked_songs(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS):
    url = SUBSONIC_URL + "/rest/getStarred"
    params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'f': 'json'
    }
    all_songs_ids = []
    data = subsonic_get_json(url, params)
    if not data:
        return []
    try:
        resp = data.get("subsonic-response", {})
        starred = resp.get('starred', {})
        starred_songs = starred.get('song', [])
        if not starred_songs:
            return []
        for star in starred_songs:
            song_id = star.get('id')
            all_songs_ids.append(song_id)
    except Exception as e:
        print(f"Error parsing starred songs: {e}")
        return []
    all_songs_ids = list(dict.fromkeys(all_songs_ids))
    return all_songs_ids

def flag_for_cleaning(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS, old_playlist_datas):
    """
    Determines which files from the PREVIOUS weekly playlist should be deleted.
    Protects files if they were liked (starred) or added to other playlists in the meantime.
    """
    playlist_or_starred = []
    in_playlist_songs = get_playlists_songs(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS)
    liked_songs = get_liked_songs(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS)
    playlist_or_starred = set(in_playlist_songs) | set(liked_songs)
    
    # deduplication
    playlist_or_starred = list(dict.fromkeys(playlist_or_starred))

    external_downloaded_subsonic = old_playlist_datas.get("subsonic_downloaded", [])
    external_downloaded_youtube = old_playlist_datas.get("youtube_downloaded", [])
    external_downloaded = set(external_downloaded_subsonic) | set(external_downloaded_youtube)

    # get list of all the ID that are starred or inside a playlist from the OLD weekly discovery
    matches = list(set(playlist_or_starred) & set(external_downloaded))
    already_local = old_playlist_datas.get("already_local", [])
    already_local_ids = [track['download_id'] for track in already_local if 'download_id' in track]

    not_delete = set(already_local_ids) | set(matches)

    all_tracks_ids = old_playlist_datas.get("all_tracks_ids", [])

    to_delete = []

    for track_id in all_tracks_ids:
        if track_id not in not_delete:
            to_delete.append(track_id)

    print(f"Starred or in playlist from weekly discovery = {matches}")
    print("-"*30)
    print(f"Already local = {already_local}")
    print("-"*30)
    print(f"Full list of id from weekly discovery = {all_tracks_ids} with {len(all_tracks_ids)}")
    print("-"*30)
    print(f"To not delete : {not_delete}")
    print(f"To delete : {to_delete}, with {len(to_delete)}")
    return to_delete

def cleaning(SUBSONIC_URL, SUBSONIC_USER, SUBSONIC_PASS, LOCAL_DOWNLOAD_PATH, to_delete):
    """
    Performs physical file deletion. Includes 'Surgical Cleaning' logic to find files 
    even if the filename doesn't perfectly match the Subsonic path.
    """
    url = SUBSONIC_URL + "/rest/getSong"
    base_params = {
        'u': SUBSONIC_USER,
        'p': SUBSONIC_PASS,
        'v': '1.16.1',
        'c': 'python-script',
        'f': 'json'
    }
    deleted_count = 0
    print(f"Starting cleanup for {len(to_delete)} items...")

    for song_id in to_delete:
        params = base_params.copy()
        params['id'] = song_id
        data = subsonic_get_json(url, params)
        
        if not data: continue
        
        resp = data.get("subsonic-response", {})
        song = resp.get('song', {})
        relative_path = song.get('path')
        title = song.get('title')
        
        if not relative_path or not title:
            continue

        # 1. Chemin théorique
        full_path_theorique = os.path.join(LOCAL_DOWNLOAD_PATH, relative_path)
        
        # 2. Suppression Directe (Match parfait)
        if os.path.exists(full_path_theorique):
            # os.remove(full_path_theorique) # <--- DECOMMENTER POUR ACTIVER
            print(f"[Deleted] Direct match: {relative_path}")
            deleted_count += 1
            continue

        # 3. Recherche de dossier (Album ou Artiste)
        target_folder = os.path.dirname(full_path_theorique)
        folders_to_check = []
        
        if os.path.exists(target_folder):
            folders_to_check.append(target_folder)
        
        parent_folder = os.path.dirname(target_folder)
        if os.path.exists(parent_folder) and parent_folder not in folders_to_check:
             if parent_folder != LOCAL_DOWNLOAD_PATH: 
                folders_to_check.append(parent_folder)

        if not folders_to_check:
            # Cas rare : dossier introuvable du tout
            # print(f"[Skip] Dossier introuvable pour : {relative_path}") 
            continue

        # Nettoyage Cible
        clean_target_title = utility.clean_title(title).lower()
        if not clean_target_title: continue

        found_in_folder = False
        
        for folder in folders_to_check:
            if found_in_folder: break
            
            try:
                files_in_dir = os.listdir(folder)
            except OSError:
                continue

            for f in files_in_dir:
                if not f.lower().endswith(('.mp3', '.flac', '.m4a', '.wav', '.opus')):
                    continue
                
                # --- V3 : NETTOYAGE CHIRURGICAL ---
                # 1. Enlever l'extension (.flac)
                name_no_ext = os.path.splitext(f)[0]
                
                # 2. Enlever les chiffres et tirets au début (ex: "01 - ", "02. ")
                # Regex : Début (^) + Chiffres (\d+) + Espace/Point/Tiret optionnels
                name_clean_prefix = re.sub(r'^\d+\s*[-.]\s*', '', name_no_ext)
                
                # 3. Nettoyage standard (accents, ponctuation restante)
                clean_filename = utility.clean_title(name_clean_prefix).lower()
                
                # --- STRATEGIE DE COMPARAISON ---

                # A. Inclusion Directe (Le titre cible est DANS le nom de fichier nettoyé)
                # Ex: Cible="Diamonds", Fichier="Diamonds" (après nettoyage du "01 -")
                # On met une sécurité sur la longueur pour ne pas matcher des mots trop courts comme "A"
                if len(clean_target_title) > 3 and clean_target_title == clean_filename:
                    score = 100
                elif len(clean_target_title) > 4 and clean_target_title in clean_filename:
                    # Ex: Cible="Forever", Fichier="Forever (Live)" -> clean="forever live" -> match
                    score = 99 
                else:
                    # B. Fuzzy Matching (Secours)
                    # partial_ratio est très bon quand l'un est inclus dans l'autre
                    score = fuzz.partial_token_set_ratio(clean_target_title, clean_filename)

                # SEUIL : 80 (Suffisant car on a bien nettoyé avant)
                if score >= 80:
                    real_file_path = os.path.join(folder, f)
                    
                    # --- ACTION ---
                    # os.remove(real_file_path) # <--- DECOMMENTER POUR ACTIVER
                    print(f"[Safe Clean] Je vais supprimer : {f}")
                    print(f"             (Cible: {title} | CleanFile: {clean_filename} | Score: {score})")
                    # --------------
                    
                    deleted_count += 1
                    found_in_folder = True
                    break 
                
                # Debug seulement si score moyen pour comprendre
                elif score > 60:
                     print(f"   [Debug] Rejeté (Score {score}): '{clean_filename}' vs '{clean_target_title}'")

    return deleted_count
