import requests
from datetime import datetime

def get_weekly_playlist_infos(LB_BASE_URL, LB_USER):
    url = f"{LB_BASE_URL}/1/user/{LB_USER}/playlists/createdfor"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        playlists = data.get("playlists", [])
        if not playlists:
            print("ListenBrainz: no playlist found in 'createdfor'")
            return None

        last_playlist = playlists[0].get("playlist")
        if not last_playlist:
            print("ListenBrainz: bad response format (missing 'playlist')")
            return None

        identifier = last_playlist.get("identifier", "")
        if not identifier:
            print("ListenBrainz: missing identifier")
            return None

        mbid = identifier.split("/")[-1]

        playlist_date = last_playlist.get("date")
        if not playlist_date:
            print("ListenBrainz: missing date")
            return None

        dt_playlist = datetime.fromisoformat(playlist_date)
        date = dt_playlist.date()
        name = f"{date} Weekly Discovery"
        return {"mbid": mbid, "name": name}
    
    except requests.exceptions.RequestException as e:
        print(f"ListenBrainz network/http error: {e}")
        return None
    except ValueError as e:
        print(f"ListenBrainz JSON/date parse error: {e}")
        return None


def get_song_in_playlist(mbid, LB_BASE_URL):
    url = f"{LB_BASE_URL}/1/playlist/{mbid}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        playlist = data["playlist"]
        tracks = playlist.get("track", [])
        # print(f"Number of tracks : {len(tracks)}")
        # print("-" * 30)
        lb_track_list = []

        for track in tracks:
            artist = track['creator']
            title = track['title']
            album = track.get('album','')
            track_append = {
                'artist': artist,
                'title': title,
                'album': album
            }
            lb_track_list.append(track_append)
        return lb_track_list

    except Exception as e:
        print(f"Error : {e}")
        return None