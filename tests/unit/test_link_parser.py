"""Tests for link_parser — written test-first."""

from playlistbot.link_parser import (
    Platform,
    extract_links,
    parse_link,
)


class TestYouTubeLinks:
    def test_standard_youtube_url(self):
        link = parse_link("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert link is not None
        assert link.platform == Platform.YOUTUBE
        assert link.track_id == "dQw4w9WgXcQ"
        assert "dQw4w9WgXcQ" in link.canonical_url

    def test_short_youtube_url(self):
        link = parse_link("https://youtu.be/dQw4w9WgXcQ")
        assert link is not None
        assert link.platform == Platform.YOUTUBE
        assert link.track_id == "dQw4w9WgXcQ"

    def test_youtube_url_in_discord_format(self):
        link = parse_link("https://www.youtube.com/watch?v=abcd1234xyz")
        assert link is not None
        assert link.platform == Platform.YOUTUBE

    def test_youtube_with_extra_params(self):
        link = parse_link("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s")
        assert link is not None
        assert link.track_id == "dQw4w9WgXcQ"


class TestYouTubeMusicLinks:
    def test_youtube_music_url(self):
        link = parse_link("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
        assert link is not None
        assert link.platform == Platform.YOUTUBE_MUSIC
        assert link.track_id == "dQw4w9WgXcQ"

    def test_youtube_radio_list_url_extracts_video_id(self):
        link = parse_link("https://www.youtube.com/watch?list=RDdQw4w9WgXcQ&v=dQw4w9WgXcQ")
        assert link is not None
        assert link.platform == Platform.YOUTUBE_MUSIC
        assert link.track_id == "dQw4w9WgXcQ"
        assert link.canonical_url == "https://music.youtube.com/watch?v=dQw4w9WgXcQ"


class TestSpotifyLinks:
    def test_spotify_track(self):
        link = parse_link("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3")
        assert link is not None
        assert link.platform == Platform.SPOTIFY
        assert link.track_id == "4uLU6hMCjMI75M1A2tKUQ3"
        assert "track/4uLU6hMCjMI75M1A2tKUQ3" in link.canonical_url

    def test_spotify_album(self):
        link = parse_link("https://open.spotify.com/album/4aawyAB9vmqVZj4QR2W7JK")
        assert link is not None
        assert link.platform == Platform.SPOTIFY
        assert link.track_id == "4aawyAB9vmqVZj4QR2W7JK"

    def test_spotify_artist(self):
        # Spotify IDs are always 22-char base62
        link = parse_link("https://open.spotify.com/artist/2w9zwq3AktTeYYx6vVC1qx")
        assert link is not None
        assert link.platform == Platform.SPOTIFY
        assert link.track_id == "2w9zwq3AktTeYYx6vVC1qx"

    def test_spotify_without_open_prefix(self):
        link = parse_link("https://spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3")
        assert link is not None
        assert link.platform == Platform.SPOTIFY


class TestAppleMusicLinks:
    def test_apple_music_song(self):
        link = parse_link("https://music.apple.com/us/album/evermore/1534087830?i=1534087881")
        assert link is not None
        assert link.platform == Platform.APPLE_MUSIC
        assert link.track_id == "1534087881"

    def test_apple_music_song_short(self):
        link = parse_link("https://music.apple.com/us/song/evermore/1534087881")
        assert link is not None
        assert link.platform == Platform.APPLE_MUSIC
        assert link.track_id == "1534087881"


class TestSoundCloudLinks:
    def test_soundcloud_url(self):
        link = parse_link("https://soundcloud.com/majorlazertv/keep-it-168")
        assert link is not None
        assert link.platform == Platform.SOUND_CLOUD

    def test_soundcloud_www(self):
        link = parse_link("https://www.soundcloud.com/majorlazertv/keep-it-168")
        assert link is not None
        assert link.platform == Platform.SOUND_CLOUD


class TestBandcampLinks:
    def test_bandcamp_track(self):
        link = parse_link("https://freedemorecords.bandcamp.com/track/example-track")
        assert link is not None
        assert link.platform == Platform.BANDCAMP

    def test_bandcamp_album(self):
        link = parse_link("https://freedemorecords.bandcamp.com/album/example-album")
        assert link is not None
        assert link.platform == Platform.BANDCAMP


class TestNewPlatforms:
    def test_deezer_track(self):
        link = parse_link("https://www.deezer.com/track/123456789")
        assert link is not None
        assert link.platform == Platform.DEEZER
        assert link.track_id == "123456789"

    def test_deezer_album(self):
        link = parse_link("https://www.deezer.com/album/987654321")
        assert link is not None
        assert link.platform == Platform.DEEZER
        assert link.track_id == "987654321"

    def test_tidal_track(self):
        link = parse_link("https://listen.tidal.com/track/123456789")
        assert link is not None
        assert link.platform == Platform.TIDAL
        assert link.track_id == "123456789"

    def test_amazon_music_track(self):
        link = parse_link("https://music.amazon.com/track/ABC123DEF")
        assert link is not None
        assert link.platform == Platform.AMAZON_MUSIC

    def test_qobuz_track(self):
        link = parse_link("https://play.qobuz.com/track/abc-def-ghi")
        assert link is not None
        assert link.platform == Platform.QOBUZ

    def test_napster_track(self):
        link = parse_link("https://us.napster.com/track/xyz-789")
        assert link is not None
        assert link.platform == Platform.NAPSTER

    def test_pandora_track(self):
        link = parse_link("https://www.pandora.com/track/abc-123")
        assert link is not None
        assert link.platform == Platform.PANDORA


class TestNonMusicLinks:
    def test_non_music_url(self):
        link = parse_link("https://google.com/search?q=music")
        assert link is None

    def test_random_discord_invite(self):
        link = parse_link("https://discord.gg/abc123")
        assert link is None


class TestExtractLinks:
    def test_multiple_links_in_text(self):
        text = (
            "Check this out: https://www.youtube.com/watch?v=dQw4w9WgXcQ "
            "and also https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3"
        )
        links = extract_links(text)
        assert len(links) == 2
        assert links[0].platform == Platform.YOUTUBE
        assert links[1].platform == Platform.SPOTIFY

    def test_message_with_no_links(self):
        links = extract_links("This is just a regular message")
        assert len(links) == 0

    def test_message_with_mixed_urls(self):
        text = (
            "Hey https://www.youtube.com/watch?v=dQw4w9WgXcQ and "
            "https://google.com and https://soundcloud.com/majorlazertv/track"
        )
        links = extract_links(text)
        assert len(links) == 2
        assert links[0].platform == Platform.YOUTUBE
        assert links[1].platform == Platform.SOUND_CLOUD

    def test_links_in_angle_brackets(self):
        text = "<https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3>"
        links = extract_links(text)
        assert len(links) == 1
        assert links[0].platform == Platform.SPOTIFY


class TestDedupCanonical:
    def test_canonical_url_dedup(self):
        url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        url2 = "https://youtu.be/dQw4w9WgXcQ"
        link1 = parse_link(url1)
        link2 = parse_link(url2)
        assert link1 is not None
        assert link2 is not None
        assert link1.canonical_url == link2.canonical_url

    def test_spotify_canonical(self):
        link = parse_link("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3")
        assert link is not None
        assert link.canonical_url == "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ3"
