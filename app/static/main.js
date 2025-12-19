console.log("VIP Engine V2 Loaded");

// 1. Definujeme logiku globálne
function vipDashboard() {
    return {
        videos: [], batches: [], tags: [],
        filters: { search: '', batch: 'All', favoritesOnly: false, quality: 'All', durationMin: 0, durationMax: 3600, sort: 'date_desc', dateMin: null, dateMax: null },
        page: 1, hasMore: true, isLoading: false,
        importProgress: { active: false, percent: 0, total: 0, done: 0, eta: 0, startTime: null },
        
        // Player State
        showPlayer: false, 
        splitScreenMode: false,
        activeVideo: null,
        activeVideo2: null,
        activePlayerIdx: 0,
        hls1: null,
        hls2: null,
        
        // Settings & Filters
        showSettings: false,
        settings: { genSpeed: 'fast', importSpeed: 'default', autoplay: true, loop: false, theme: 'dark', accentColor: 'purple', playbackSpeed: 1.0, useHls: false, aria2MaxConnections: 32, aria2SplitCount: 32, aria2MaxConcurrent: 20, aria2MinSplitSize: '1M' },
        vFilters: { brightness: 100, contrast: 100, saturate: 100, zoom: 1, preset: 'none' },
        
        // UI State
        hoverVideoId: null, previewIndex: 0, showFilters: false, showImportModal: false, importTextContent: '', newBatchName: '', selectedParser: 'yt-dlp',
        batchMode: false, selectedIds: [], toasts: [], polling: null, dragCounter: 0, showShortcutsModal: false,

        // Smart Playlist State
        smartPlaylists: [],
        activeSmartPlaylistId: null,
        showSmartPlaylistModal: false,
        editingPlaylistId: null,
        smartPlaylistForm: {
            name: '',
            rules: []
        },
        
        // Command Palette
        showCommandPalette: false,
        commandQuery: '',
        commandResults: [],
        isCommandSearching: false,
        commands: [
            { id: 'cmd_theme', type: 'command', title: 'Toggle Light/Dark Theme', icon: 'contrast', action: function() { this.settings.theme = this.settings.theme === 'dark' ? 'light' : 'dark'; this.showCommandPalette = false; } },
            { id: 'cmd_batch', type: 'command', title: 'Toggle Batch Mode', icon: 'checklist', action: function() { this.toggleBatchMode(); this.showCommandPalette = false; } },
            { id: 'cmd_fav', type: 'command', title: 'Show Favorites', icon: 'favorite', action: function() { this.filters.favoritesOnly = true; this.loadVideos(true); this.showCommandPalette = false; } },
            { id: 'cmd_home', type: 'command', title: 'Show All Videos', icon: 'dashboard', action: function() { this.filters.favoritesOnly = false; this.loadVideos(true); this.showCommandPalette = false; } },
            { id: 'cmd_splitscreen', type: 'command', title: 'Toggle Split Screen', icon: 'view_column', action: function() { if(this.showPlayer) this.toggleSplitScreen(); else this.showToast('Player must be open'); this.showCommandPalette = false; } },
            { id: 'cmd_random', type: 'command', title: 'Play Random Video', icon: 'shuffle', action: function() { this.playRandomVideo(); this.showCommandPalette = false; } }
        ],

        playRandomVideo() {
            if (this.videos.length > 0) {
                const randomIndex = Math.floor(Math.random() * this.videos.length);
                this.playVideo(this.videos[randomIndex]);
            } else {
                this.showToast('No videos available to play.', 'info');
            }
        },

        // Eporner modal state
        showEpornerModal: false,
        epornerUrl: '',
        epornerQuery: '',
        epornerCount: 50,
        epornerMinQuality: 1080,
        // CSV modal state
        showCSVModal: false,
        // Coomer modal state
        showCoomerModal: false,
        coomerUrl: '',
        coomerProfileData: null,
        coomerSelectedVideos: [],
        isScanningCoomer: false,
        
        // Turbo Download state
        turboDownloads: {}, // gid -> {video_id, status, progress}
        turboDownloadPolling: null,

        // XVideos Import
        async handleXVideosFileSelect(e) {
            const file = e.target.files[0];
            if (!file) return;

            if (file.type !== "text/plain" && !file.name.endsWith('.txt')) {
                this.showToast("Only .txt files allowed", "error", "error");
                return;
            }

            const text = await file.text();
            const urls = text.split(/\r?\n/).filter(line => line.trim().length > 0 && line.includes('xvideos.com'));
            
            if (urls.length === 0) {
                this.showToast("No valid XVideos URLs found", "warning", "warning");
                return;
            }

            this.showToast(`Importing ${urls.length} videos...`, "cloud_download");
            this.importProgress.active = true;
            this.importProgress.total = urls.length;
            this.importProgress.done = 0;
            this.importProgress.percent = 0;
            this.importProgress.eta = 0;
            this.importProgress.startTime = Date.now();

            // Process sequentially-ish (async loop)
            for (const url of urls) {
                try {
                    const res = await fetch('/api/import/xvideos', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url.trim() })
                    });
                    
                    if (res.ok) {
                        const meta = await res.json();
                        // Map to internal video object structure if needed, or just reload?
                        // The user wants "priebežne renderuje UI" (progressively render).
                        // So we should add it to this.videos immediately.
                        
                        // Construct video object compatible with UI
                        const newVideo = {
                            id: meta.db_id || meta.id, // Use DB ID if available, else Source ID (might break things if not int)
                            title: meta.title,
                            thumbnail_path: meta.thumbnail,
                            duration: meta.duration,
                            height: meta.stream.height,
                            width: 0, 
                            status: 'ready',
                            url: meta.stream.url, // HLS URL
                            source_url: meta.source_url || url.trim(),
                            created_at: new Date().toISOString(),
                            tags: '',
                            ai_tags: ''
                        };

                        // Check if already in list to avoid duplicates visually
                        if (!this.videos.find(v => v.id === newVideo.id)) {
                             this.videos.unshift(newVideo);
                        }
                    } else {
                        console.error(`Failed to import ${url}`);
                    }
                } catch (err) {
                    console.error(`Error importing ${url}:`, err);
                }
                
                this.importProgress.done++;
                this.importProgress.percent = Math.round((this.importProgress.done / this.importProgress.total) * 100);
                
                // Calculate ETA
                if (this.importProgress.done > 0 && this.importProgress.startTime) {
                    const elapsed = (Date.now() - this.importProgress.startTime) / 1000; // seconds
                    const avgTimePerItem = elapsed / this.importProgress.done;
                    const remaining = this.importProgress.total - this.importProgress.done;
                    this.importProgress.eta = Math.ceil(remaining * avgTimePerItem);
                }
            }

            this.showToast("Import completed", "check_circle", "success");
            setTimeout(() => { this.importProgress.active = false; }, 1000);
            
            // Clear input
            e.target.value = '';
        },

        init() {
            this.loadSettings();
            this.loadBatches(); this.loadTags(); this.loadVideos(true); this.setupKeys();
            this.loadSmartPlaylists();
            this.connectWebSocket();
            
            // Apply loaded settings
            document.body.className = (this.settings.theme || 'dark') + '-theme';
            document.body.dataset.accent = this.settings.accentColor || 'purple';

            this.$watch('settings.theme', (theme) => { document.body.className = theme + '-theme'; });
            this.$watch('settings.accentColor', (color) => { document.body.dataset.accent = color; });
            
            this.$watch('commandQuery', (q) => this.runCommandSearch(q));
            this.$watch('showCommandPalette', (visible) => { if(visible) { this.commandQuery = ''; this.$nextTick(() => this.$refs.commandInput.focus()); } });

            // --- DRAG & DROP EVENTS ---
            window.addEventListener('dragenter', (e) => { 
                e.preventDefault(); 
                this.dragCounter++; 
            });
            window.addEventListener('dragleave', (e) => { 
                e.preventDefault(); 
                this.dragCounter = Math.max(0, this.dragCounter - 1); 
            });
            window.addEventListener('dragover', (e) => { 
                e.preventDefault(); 
            });
            window.addEventListener('drop', (e) => { 
                e.preventDefault(); 
                this.dragCounter = 0; 
                this.handleDrop(e); 
            });
        },

        get exportUrl() {
            const params = new URLSearchParams();
            Object.entries(this.filters).forEach(([key, value]) => {
                if (value !== null && value !== '' && value.toString() !== 'All') {
                    params.append(key, value);
                }
            });
            return `/api/export?${params.toString()}`;
        },

        // --- Smart Playlist Functions ---
        async loadSmartPlaylists() {
            try {
                const res = await fetch('/api/smart-playlists');
                this.smartPlaylists = await res.json();
            } catch (e) {
                console.error('Failed to load smart playlists', e);
            }
        },

        async loadSmartPlaylist(playlistId) {
            this.activeSmartPlaylistId = playlistId;
            this.isLoading = true;
            try {
                const res = await fetch(`/api/smart-playlists/${playlistId}/videos`);
                this.videos = await res.json();
                this.hasMore = false; // Smart playlists load all at once
            } catch (e) {
                this.showToast('Failed to load playlist videos', 'error', 'error');
            } finally {
                this.isLoading = false;
            }
        },
        
        openSmartPlaylistModal(playlist = null) {
            if (playlist) {
                this.editingPlaylistId = playlist.id;
                this.smartPlaylistForm.name = playlist.name;
                this.smartPlaylistForm.rules = JSON.parse(JSON.stringify(playlist.rules));
            } else {
                this.editingPlaylistId = null;
                this.smartPlaylistForm.name = '';
                this.smartPlaylistForm.rules = [{ field: 'title', operator: 'contains', value: '' }];
            }
            this.showSmartPlaylistModal = true;
        },

        addSmartPlaylistRule() {
            this.smartPlaylistForm.rules.push({ field: 'title', operator: 'contains', value: '' });
        },

        removeSmartPlaylistRule(index) {
            this.smartPlaylistForm.rules.splice(index, 1);
        },

        async saveSmartPlaylist() {
            const method = this.editingPlaylistId ? 'PUT' : 'POST';
            const url = this.editingPlaylistId ? `/api/smart-playlists/${this.editingPlaylistId}` : '/api/smart-playlists';
            
            try {
                const res = await fetch(url, {
                    method: method,
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.smartPlaylistForm)
                });
                if (res.ok) {
                    this.showToast('Playlist saved', 'check_circle', 'success');
                    this.showSmartPlaylistModal = false;
                    this.loadSmartPlaylists();
                } else {
                    this.showToast('Failed to save playlist', 'error', 'error');
                }
            } catch (e) {
                this.showToast('An error occurred', 'error', 'error');
            }
        },

        async loadTags() {
            try {
                const res = await fetch('/api/tags');
                this.tags = await res.json();
            } catch (e) {
                console.error('Failed to load tags', e);
            }
        },
        
        setTagFilter(tag) {
            this.filters.search = tag;
            this.loadVideos(true);
        },

        runCommandSearch(q) {
            if (!q.trim()) {
                this.commandResults = this.commands.map(c => ({...c, type: 'command'}));
                this.isCommandSearching = false;
                return;
            }
            this.isCommandSearching = true;

            if (q.startsWith('>')) {
                const commandQuery = q.substring(1).toLowerCase();
                this.commandResults = this.commands.filter(c => c.title.toLowerCase().includes(commandQuery));
                this.isCommandSearching = false;
                return;
            }
            
            this.searchVideos(q).then(videos => {
                const videoResults = videos.map(v => ({...v, type: 'video'}));
                const filteredCommands = this.commands.filter(c => c.title.toLowerCase().includes(q.toLowerCase()));
                this.commandResults = [...filteredCommands, ...videoResults];
                this.isCommandSearching = false;
            });
        },
        
        executeCommandResult(result) {
            if (!result) return;
            if (result.type === 'command') {
                result.action.call(this); 
            } else {
                this.playVideo(result);
            }
            this.showCommandPalette = false;
            this.commandQuery = '';
        },

        setupKeys() {
            window.addEventListener('keydown', (e) => {
                if(e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;                
                if(e.ctrlKey && e.code === 'KeyK') { e.preventDefault(); this.showCommandPalette = true; }

                if (this.showPlayer) {
                    if(e.code === 'Space') { e.preventDefault(); this.togglePlay(); }
                    if(e.code === 'KeyS') this.toggleSplitScreen();
                    if(e.code === 'KeyF') this.toggleFullscreen();
                    if(e.code === 'KeyI') this.togglePip();
                }
                
                if(e.code === 'Escape') {
                    if(this.showCommandPalette) this.showCommandPalette = false;
                    else if(this.showSettings) { this.showSettings = false; this.saveSettings(); }
                    else if(this.showPlayer) this.closePlayer();
                    else if(this.showImportModal) this.showImportModal = false;
                    else if(this.batchMode) this.toggleBatchMode(false);
                }
            });
        },
        
        togglePlay() {
            const v1 = this.$refs.videoPlayer1;
            const v2 = this.$refs.videoPlayer2;
            if(this.activePlayerIdx === 0 && v1) v1.paused ? v1.play() : v1.pause();
            if(this.activePlayerIdx === 1 && v2) v2.paused ? v2.play() : v2.pause();
        },

        async searchVideos(query) {
            if (!query || query.length < 2) return [];
            const params = new URLSearchParams({ query: query });
            try {
                const res = await fetch(`/api/search/subtitles?${params}`);
                return await res.json();
            } catch (e) {
                this.showToast('Video search failed', 'error', 'error');
                return [];
            }
        },

        highlight(text, query) {
            if (!text || !query) return (text || '').substring(0, 200);
            const index = text.toLowerCase().indexOf(query.toLowerCase());
            if (index === -1) return text.substring(0, 200) + '...';
            
            const start = Math.max(0, index - 50);
            const end = Math.min(text.length, index + query.length + 50);
            const snippet = text.substring(start, end);
            
            const highlighted = snippet.replace(new RegExp(query, 'gi'), (match) => `<mark>${match}</mark>`);
            return `...${highlighted}...`;
        },

        loadSettings() {
            const s = localStorage.getItem('vipSettings');
            if(s) {
                this.settings = { ...this.settings, ...JSON.parse(s) };
            }
            // Load Aria2c config from server
            this.loadAria2Config();
        },
        saveSettings() {
            localStorage.setItem('vipSettings', JSON.stringify(this.settings));
            this.showToast('Settings saved', 'check_circle', 'success');
        },
        async loadAria2Config() {
            try {
                const res = await fetch('/api/aria2/config');
                if (res.ok) {
                    const config = await res.json();
                    // Update settings with current Aria2c config
                    this.settings.aria2MaxConnections = config.max_connections_per_server || 32;
                    this.settings.aria2SplitCount = config.split_count || 32;
                    this.settings.aria2MaxConcurrent = config.max_concurrent_downloads || 20;
                    this.settings.aria2MinSplitSize = config.min_split_size || '1M';
                }
            } catch (e) {
                console.error('Error loading Aria2c config:', e);
            }
        },
        async saveAria2Config() {
            try {
                const res = await fetch('/api/aria2/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        max_connections_per_server: this.settings.aria2MaxConnections || 32,
                        split_count: this.settings.aria2SplitCount || 32,
                        max_concurrent_downloads: this.settings.aria2MaxConcurrent || 20,
                        min_split_size: this.settings.aria2MinSplitSize || '1M'
                    })
                });
                if (res.ok) {
                    this.saveSettings(); // Also save to localStorage
                    this.showToast('Aria2c settings saved! Restart Aria2c daemon for changes to take effect.', 'check_circle', 'success');
                } else {
                    this.showToast('Failed to save Aria2c settings', 'error', 'error');
                }
            } catch (e) {
                this.showToast('Error saving Aria2c settings: ' + e.message, 'error', 'error');
            }
        },

        handleCardClick(video) { 
            if(this.batchMode) { 
                if(this.selectedIds.includes(video.id)) this.selectedIds = this.selectedIds.filter(id => id !== video.id); 
                else this.selectedIds.push(video.id); 
            } else { 
                this.playVideo(video); 
            } 
        },

        // --- SMART PLAYER INITIALIZATION (FIXED) ---
        async initPlayer(video, playerIdx) {
            const videoRef = playerIdx === 0 ? this.$refs.videoPlayer1 : this.$refs.videoPlayer2;
            if (!videoRef) return;

            // Cleanup previous HLS instances
            if (playerIdx === 0 && this.hls1) { this.hls1.destroy(); this.hls1 = null; }
            if (playerIdx === 1 && this.hls2) { this.hls2.destroy(); this.hls2 = null; }

            // Reset player state
            videoRef.removeAttribute('src'); 
            videoRef.load();

            // Logic for XVideos / HLS
            const isXVideos = video.source_url && video.source_url.includes('xvideos');
            // Check if HLS: URL contains m3u8 OR it is from XVideos (enforced HLS)
            const isHls = video.url.includes('.m3u8') || isXVideos; 

            // IMPORTANT: If importing from xvideos, we used the HLS URL directly in `video.url` but usually without `/stream_proxy/`.
            // The `import_xvideos` backend sets `video.url` to the HLS URL (https://video-hls...).
            // The proxy `/stream_proxy/{id}.mp4` handles redirecting/streaming that content.
            // If `video.url` is already a remote HTTP link, the proxy handles it.
            // However, HLS streams (.m3u8) usually need to be fetched directly by HLS.js (CORS might be an issue if not proxied).
            // But `stream_proxy` endpoint in `main.py` is designed to stream content.
            // Streaming an m3u8 via a single endpoint `.../stream_proxy/...mp4` might fail if m3u8 has relative paths for segments.
            // If `video.url` is an m3u8, the backend proxying it as a single file stream won't work for HLS.js which needs to parse it and fetch segments.
            // The existing code: `const src = /stream_proxy/${video.id}.mp4;`
            // If the backend `proxy_video` just streams the content of the URL, and the URL is an m3u8 file:
            // HLS.js will get the m3u8 content. Then it parses it.
            // If the m3u8 contains relative paths, HLS.js will try to fetch them relative to `/stream_proxy/`.
            // The backend proxy doesn't handle `/stream_proxy/segment-1.ts`.
            // So, for HLS to work, we usually need to pass the *original* URL to HLS.js if CORS allows, OR have a sophisticated proxy.
            // Given "yt-dlp rieši tokeny a CDN" and "NEsťahuje videá", and "vkladá videá do dashboardu", 
            // and the JSON response shape has `stream: { url: ... }`.
            // If the XVideos HLS URL is public/signed (which `yt-dlp -g` gives), we should probably use it directly if CORS permits.
            // If CORS blocks it, we need a proxy.
            // The prompt says: "backend spracúva URL... frontend... použi HLS.js... Auto-play OFF, mute ON".
            // It doesn't explicitly mention CORS.
            // However, `video.url` in DB is set to the HLS URL.
            // The existing `stream_proxy` implementation seems to be a simple pass-through stream.
            // If I use `/stream_proxy/...` as source for HLS.js, it might fail on segments.
            // LET'S ASSUME: We should try to use the `video.url` (the remote HLS url) directly for HLS.js if it's an XVideos import.
            // BUT mixed content (HTTP vs HTTPS) or CORS might trigger.
            // If `video.url` starts with http, we use it?
            
            // Let's modify logic:
            // If isXVideos, we prefer using the direct URL if possible, or we need to check if the existing proxy handles it. 
            // The existing proxy implementation is: `StreamingResponse(upstream_response.content.iter_chunked...)`.
            // This is for a single file. It won't handle HLS segments.
            // So for HLS, we MUST use the direct URL.
            
            let src = `/stream_proxy/${video.id}.mp4`;
            if (isHls && video.url.startsWith('http')) {
                src = video.url;
            }

            if (isHls && Hls.isSupported()) {
                const hls = new Hls();
                hls.loadSource(src);
                hls.attachMedia(videoRef);
                
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                     // Auto-play OFF for XVideos as per requirements
                    if (this.settings.autoplay && !isXVideos) {
                        videoRef.play().catch(e => console.warn("Autoplay was blocked"));
                    }
                });
                
                hls.on(Hls.Events.ERROR, (event, data) => {
                    if (data.fatal) {
                        console.error('HLS.js fatal error:', data);
                        // Fallback?
                    }
                });
                if (playerIdx === 0) this.hls1 = hls;
                else this.hls2 = hls;
            } else {
                // For MP4 or native HLS playback
                videoRef.src = src;
                if (this.settings.autoplay && !isXVideos) videoRef.play().catch(e => console.warn("Autoplay was blocked"));
            }
            
            // Mute ON for XVideos
            if (isXVideos) {
                videoRef.muted = true;
            } else {
                videoRef.muted = false; // Or user preference? Resetting to default.
            }

            videoRef.playbackRate = parseFloat(this.settings.playbackSpeed);
            if (video.resume_time > 5) videoRef.currentTime = video.resume_time;
        },

        playVideo(video) {
            if(video.status !== 'ready') return this.showToast("Video processing...", 'hourglass_empty');
            
            if (this.showPlayer && this.splitScreenMode) {
                if (this.activePlayerIdx === 1) {
                    this.activeVideo2 = video;
                    this.$nextTick(() => this.initPlayer(video, 1));
                }
                else {
                    this.activeVideo = video;
                    this.$nextTick(() => this.initPlayer(video, 0));
                }
            } else {
                this.activeVideo = video;
                this.showPlayer = true;
                this.$nextTick(() => this.initPlayer(video, 0));
            }
            
            this.showCommandPalette = false;
        },

        getPlayerStyle(idx) {
            const f = this.vFilters;
            let filter = `brightness(${f.brightness}%) contrast(${f.contrast}%) saturate(${f.saturate}%)`;
            
            switch (f.preset) {
                case 'grayscale': filter += ' grayscale(100%)'; break;
                case 'sepia': filter += ' sepia(100%)'; break;
                case 'invert': filter += ' invert(100%)'; break;
                case 'thermal': filter = 'contrast(200%) hue-rotate(280deg)'; break;
                case 'nightvision': filter = 'grayscale(100%) brightness(170%) contrast(1.5) sepia(20%) hue-rotate(50deg)'; break;
            }

            return `filter: ${filter}; transform: scale(${f.zoom}); transform-origin: center;`;
        },
        
        resetFilters() {
            this.vFilters = { brightness: 100, contrast: 100, saturate: 100, zoom: 1, preset: 'none' };
        },

        screenshot(idx) {
            const vid = idx === 0 ? this.$refs.videoPlayer1 : this.$refs.videoPlayer2;
            if(!vid) return;
            const canvas = document.createElement('canvas');
            canvas.width = vid.videoWidth; canvas.height = vid.videoHeight;
            canvas.getContext('2d').drawImage(vid, 0, 0);
            const link = document.createElement('a');
            link.download = `snap_${Date.now()}.jpg`;
            link.href = canvas.toDataURL('image/jpeg');
            link.click();
            this.showToast("Screenshot saved", 'camera_alt');
        },

        toggleSplitScreen() {
            this.splitScreenMode = !this.splitScreenMode;
            if(!this.splitScreenMode) {
                this.activeVideo2 = null;
                if(this.hls2) { this.hls2.destroy(); this.hls2 = null; }
            }
            this.showToast(this.splitScreenMode ? "Split Screen ON" : "Split Screen OFF", 'view_column');
        },

        async regenerateThumb(video) {
            this.showToast("Regenerating...", 'refresh');
            try {
                // Pass the preference mode (hls or mp4)
                const mode = this.settings.useHls ? 'hls' : 'mp4';
                const res = await fetch(`/api/videos/${video.id}/regenerate?mode=${mode}`, { method: 'POST' });
                if(res.ok) {
                    this.showToast("Queued for processing", 'check_circle');
                    video.status = 'processing';
                    this.startPolling();
                }
            } catch(e) { this.showToast("Error starting regeneration", 'error', 'error'); }
        },

        // Turbo Download functions
        async turboDownload(videoId) {
            if (!videoId) {
                this.showToast('No video selected', 'error', 'error');
                return;
            }
            
            this.showToast('Starting turbo download...', 'bolt', 'info');
            
            try {
                const res = await fetch('/api/videos/turbo-download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_ids: [videoId] })
                });
                
                const data = await res.json();
                
                if (res.ok) {
                    const result = data.results[0];
                    if (result.status === 'started') {
                        this.showToast('Turbo download started!', 'bolt', 'success');
                        this.startTurboDownloadPolling();
                    } else {
                        this.showToast(`Download failed: ${result.reason}`, 'error', 'error');
                    }
                } else {
                    this.showToast(data.error || 'Failed to start turbo download', 'error', 'error');
                }
            } catch (e) {
                this.showToast('Error starting turbo download: ' + e.message, 'error', 'error');
            }
        },
        
        async turboDownloadBatch() {
            if (this.selectedIds.length === 0) {
                this.showToast('No videos selected', 'error', 'error');
                return;
            }
            
            this.showToast(`Starting turbo download for ${this.selectedIds.length} videos...`, 'bolt', 'info');
            
            try {
                const res = await fetch('/api/videos/turbo-download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_ids: this.selectedIds })
                });
                
                const data = await res.json();
                
                if (res.ok) {
                    const started = data.results.filter(r => r.status === 'started').length;
                    this.showToast(`Turbo download started for ${started} videos!`, 'bolt', 'success');
                    this.startTurboDownloadPolling();
                    this.selectedIds = []; // Clear selection
                    this.batchMode = false;
                } else {
                    this.showToast(data.error || 'Failed to start turbo download', 'error', 'error');
                }
            } catch (e) {
                this.showToast('Error starting turbo download: ' + e.message, 'error', 'error');
            }
        },
        
        startTurboDownloadPolling() {
            if (this.turboDownloadPolling) return; // Already polling
            
            this.turboDownloadPolling = setInterval(async () => {
                try {
                    const res = await fetch('/api/videos/turbo-download/status');
                    const data = await res.json();
                    
                    if (res.ok && data.downloads) {
                        // Update turbo downloads state
                        data.downloads.forEach(dl => {
                            const completed = parseInt(dl.completedLength || 0);
                            const total = parseInt(dl.totalLength || 0);
                            const speed = parseInt(dl.downloadSpeed || 0);
                            const progress = total > 0 ? (completed / total * 100) : 0;
                            
                            // Calculate ETA
                            let eta = 0;
                            if (speed > 0 && total > completed && dl.status === 'active') {
                                const remaining = total - completed;
                                eta = Math.ceil(remaining / speed); // seconds
                            }
                            
                            // Check if download failed (too small file, error, etc.)
                            const isError = dl.status === 'error' || dl.errorCode || 
                                          (dl.status === 'complete' && completed > 0 && completed < 1024 * 1024);
                            
                            // Get or create download entry
                            if (!this.turboDownloads[dl.gid]) {
                                this.turboDownloads[dl.gid] = { startTime: Date.now(), errorShown: false };
                            }
                            
                            this.turboDownloads[dl.gid] = {
                                ...this.turboDownloads[dl.gid],
                                video_id: dl.video_id,
                                status: isError ? 'error' : dl.status,
                                progress: parseFloat(progress.toFixed(1)),
                                speed: this.formatBytes(speed) + '/s',
                                eta: eta,
                                completed: this.formatBytes(completed),
                                total: this.formatBytes(total),
                                error: dl.errorMessage || (isError && completed < 1024 * 1024 ? 'File too small - likely error page' : null)
                            };
                            
                            // Show error toast if download failed
                            if (isError && !this.turboDownloads[dl.gid].errorShown) {
                                this.turboDownloads[dl.gid].errorShown = true;
                                this.showToast(`Download failed for video ${dl.video_id}: ${dl.errorMessage || 'File too small - likely error page'}`, 'error', 'error');
                            }
                        });
                        
                        // Remove completed downloads (non-error) after 5 seconds
                        Object.keys(this.turboDownloads).forEach(gid => {
                            const dl = data.downloads.find(d => d.gid === gid);
                            if (!dl || (dl.status === 'complete' && !dl.errorCode)) {
                                setTimeout(() => {
                                    delete this.turboDownloads[gid];
                                }, 5000);
                            }
                        });
                        
                        // Check if any downloads are still active
                        const active = data.downloads.filter(dl => dl.status === 'active' || dl.status === 'waiting');
                        if (active.length === 0 && Object.keys(this.turboDownloads).length === 0) {
                            // All downloads finished, stop polling
                            this.stopTurboDownloadPolling();
                        }
                    }
                } catch (e) {
                    console.error('Error polling turbo download status:', e);
                }
            }, 2000); // Poll every 2 seconds
        },
        
        stopTurboDownloadPolling() {
            if (this.turboDownloadPolling) {
                clearInterval(this.turboDownloadPolling);
                this.turboDownloadPolling = null;
            }
        },
        
        formatBytes(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
        },
        
        formatTime(seconds) {
            if (!seconds || seconds <= 0) return '--';
            if (seconds < 60) return `${Math.ceil(seconds)}s`;
            if (seconds < 3600) {
                const mins = Math.floor(seconds / 60);
                const secs = Math.ceil(seconds % 60);
                return `${mins}m ${secs}s`;
            }
            const hours = Math.floor(seconds / 3600);
            const mins = Math.floor((seconds % 3600) / 60);
            return `${hours}h ${mins}m`;
        },
        
        updateImportETA() {
            if (this.importProgress.done > 0 && this.importProgress.startTime) {
                const elapsed = (Date.now() - this.importProgress.startTime) / 1000; // seconds
                const avgTimePerItem = elapsed / this.importProgress.done;
                const remaining = this.importProgress.total - this.importProgress.done;
                this.importProgress.eta = Math.ceil(remaining * avgTimePerItem);
            }
        },
        
        formatTime(seconds) {
            if (!seconds || seconds <= 0) return '--';
            if (seconds < 60) return `${Math.ceil(seconds)}s`;
            if (seconds < 3600) {
                const mins = Math.floor(seconds / 60);
                const secs = Math.ceil(seconds % 60);
                return `${mins}m ${secs}s`;
            }
            const hours = Math.floor(seconds / 3600);
            const mins = Math.floor((seconds % 3600) / 60);
            return `${hours}h ${mins}m`;
        },
        
        async regenerateThumbAll() {
            if (!this.selectedIds.length) return;
            this.showToast("Batch regenerating...", 'refresh');
            const mode = this.settings.useHls ? 'hls' : 'mp4';
            for (const id of this.selectedIds) {
                try {
                    await fetch(`/api/videos/${id}/regenerate?mode=${mode}`, { method: 'POST' });
                } catch(e) {}
            }
            this.showToast("Všetky označené videá boli poslané na regeneráciu", 'check_circle');
            this.startPolling();
        },

        async handleFileSelect(e) { 
            const files = e.target.files; 
            if (files && files.length > 0) await this.uploadFiles(files); 
        },
        async handleDrop(e) { 
            const files = e.dataTransfer.files; 
            if (files && files.length > 0) await this.uploadFiles(files); 
        },
        async uploadFiles(files) {
            const allowedExt = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.txt', '.json', '.csv'];
            let anyUploaded = false;
            this.importProgress.active = true;
            this.importProgress.total = files.length;
            this.importProgress.done = 0;
            this.importProgress.percent = 0;
            this.importProgress.eta = 0;
            this.importProgress.startTime = Date.now();
            this.importProgress.eta = 0;
            this.importProgress.startTime = Date.now();
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
                if (!allowedExt.includes(ext)) {
                    this.showToast(`Nepodporovaný typ: ${file.name}`, 'error', 'error');
                    continue;
                }
                anyUploaded = true;
                const fd = new FormData(); fd.append('file', file);
                this.showToast(`Uploading ${file.name}...`, 'cloud_upload');
                try { 
                    await fetch('/api/import/file', { method: 'POST', body: fd }); 
                    this.showToast(`Import ${file.name} started`, 'check_circle'); 
                    this.importProgress.done++;
                    this.importProgress.percent = Math.round((this.importProgress.done / this.importProgress.total) * 100);
                    this.updateImportETA();
                } catch(e) { 
                    this.showToast(`Failed: ${file.name}`, 'error', 'error'); 
                    this.importProgress.done++;
                    this.importProgress.percent = Math.round((this.importProgress.done / this.importProgress.total) * 100);
                    this.updateImportETA();
                }
            }
            setTimeout(() => { this.importProgress.active = false; }, 800);
            if (anyUploaded) setTimeout(() => { this.loadBatches(); this.loadVideos(true); }, 1000);
        },

        async handleCSVSelect(e) {
            const files = e.target.files;
            if (files && files.length > 0) await this.uploadFiles(files);
            this.showCSVModal = false;
        },
        
        async loadVideos(reset = false) {
            if (reset) { this.videos = []; this.page = 1; this.hasMore = true; }
            if (this.isLoading && !reset) return;
            this.isLoading = true;

            const params = new URLSearchParams({ ...this.filters, page: this.page, limit: 10 });
            try {
                const res = await fetch(`/api/videos?${params}`);
                const data = await res.json();
                if (data.length === 0) this.hasMore = false;
                else {
                    const newItems = data.filter(n => !this.videos.some(e => e.id === n.id));
                    this.videos = reset ? data : [...this.videos, ...newItems];
                    this.page++;
                }
            } catch(e) {} finally { this.isLoading = false; }
        },
        connectWebSocket() {
            const wsUrl = `ws://${window.location.host}/ws/status`;
            const socket = new WebSocket(wsUrl);

            socket.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'status_update') {
                    const video = this.videos.find(v => v.id === data.video_id);
                    if (video) {
                        video.status = data.status;
                        if (data.status === 'ready') {
                            if (data.title) video.title = data.title;
                            if (data.thumbnail_path) video.thumbnail_path = data.thumbnail_path;
                            this.showToast(`Ready: ${video.title}`, 'check_circle', 'success');
                        } else if (data.status === 'error') {
                            this.showToast(`Error: ${video.title}`, 'error', 'error');
                        }
                    }
                }
            };

            socket.onclose = () => {
                console.log('WebSocket disconnected. Reconnecting...');
                setTimeout(() => this.connectWebSocket(), 3000);
            };

            socket.onerror = (error) => {
                console.error('WebSocket error:', error);
                socket.close();
            };
        },
        async deleteCurrentBatch() {
            if(this.filters.batch === 'All' || !confirm("Delete ALL videos in batch?")) return;
            await fetch('/api/batch/delete-all', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ batch_name: this.filters.batch }) });
            this.showToast("Batch deleted", 'delete'); this.filters.batch = 'All'; this.loadBatches(); this.loadVideos(true);
        },
        toggleBatchMode(s) { this.batchMode = s !== undefined ? s : !this.batchMode; if(!this.batchMode) this.selectedIds = []; },
        async runBatch(a) { if(confirm(`Action: ${a}?`)) { await fetch('/api/batch-action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ video_ids: this.selectedIds, action: a }) }); this.toggleBatchMode(false); this.loadVideos(true); } },
        closePlayer() { 
            this.showPlayer = false; 
            this.activeVideo = null; 
            this.activeVideo2 = null; 
            this.splitScreenMode = false;
            if(this.hls1) { this.hls1.destroy(); this.hls1 = null; }
            if(this.hls2) { this.hls2.destroy(); this.hls2 = null; }
        },
        async loadBatches() { try { this.batches = await (await fetch('/api/batches')).json(); } catch(e){} },
        async importFromText() { 
            const batch = this.newBatchName || 'Import ' + new Date().toLocaleTimeString();
            try {
                this.importProgress.active = true;
                this.importProgress.percent = 0;
                const resp = await fetch('/api/import/text', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        urls: this.importTextContent.split('\n'),
                        batch_name: batch,
                        parser: this.selectedParser,
                        import_speed: this.settings.importSpeed || 'default'
                    })
                });
                const data = await resp.json();
                this.showImportModal = false;
                if (data && data.count !== undefined) {
                    this.showToast(`Pridaných ${data.count} videí do batchu '${data.batch}'`, 'check_circle');
                    this.importProgress.percent = 100;
                } else {
                    this.showToast("Import spustený", 'cloud_queue');
                    this.importProgress.percent = 100;
                }
                this.loadBatches();
                this.loadVideos(true);
                setTimeout(() => { this.importProgress.active = false; }, 800);
            } catch (e) {
                this.showToast("Chyba pri importe", 'error', 'error');
                this.importProgress.percent = 100;
                setTimeout(() => { this.importProgress.active = false; }, 800);
            }
        },
        importEporner() {
            const hasPlaylistUrl = this.epornerUrl && this.epornerUrl.trim().length > 0;
            const hasQuery = this.epornerQuery && this.epornerQuery.trim().length >= 2;
            
            if(!hasPlaylistUrl && !hasQuery) {
                this.showToast('Zadaj buď playlist URL alebo vyhľadávací výraz!', 'error');
                return;
            }
            
            this.importProgress.active = true;
            this.importProgress.percent = 0;
            this.showEpornerModal = false;
            this.showToast(hasPlaylistUrl ? 'Importujem Eporner playlist...' : 'Importujem Eporner videá...');
            
            const requestBody = {
                count: this.epornerCount,
                min_quality: this.epornerMinQuality,
                batch_name: '',
                import_speed: this.settings.importSpeed || 'default'
            };
            
            if(hasPlaylistUrl) {
                requestBody.playlist_url = this.epornerUrl.trim();
            } else {
                requestBody.query = this.epornerQuery.trim();
            }
            
            fetch('/api/import/eporner_search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            })
            .then(r => {
                if(!r.ok) {
                    return r.json().then(err => Promise.reject(err));
                }
                return r.json();
            })
            .then(res => {
                this.showToast(`Pridaných ${res.count} videí z Eporner!`, 'success');
                this.loadBatches();
                this.loadVideos(true);
                this.importProgress.percent = 100;
                setTimeout(() => { this.importProgress.active = false; }, 800);
                // Reset form
                this.epornerUrl = '';
                this.epornerQuery = '';
            })
            .catch(e => {
                const errorMsg = e.error || e.message || 'Chyba pri importe z Eporner!';
                this.showToast(errorMsg, 'error');
                this.importProgress.percent = 100;
                setTimeout(() => { this.importProgress.active = false; }, 800);
            });
        },
        async scanCoomerProfile() {
            if(!this.coomerUrl || this.coomerUrl.trim().length < 3) {
                this.showToast('Zadaj URL alebo username!', 'error', 'error');
                return;
            }
            
            // Backend will handle domain validation, so we don't need strict frontend validation
            // Just check if it looks like a URL or username
            
            this.isScanningCoomer = true;
            this.coomerProfileData = null;
            this.coomerSelectedVideos = [];
            this.showToast('Skenujem Coomer/Kemono profil...', 'search');
            
            try {
                const res = await fetch('/api/import/coomer/scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profile_url: this.coomerUrl.trim() })
                });
                const data = await res.json();
                
                if(res.ok && data) {
                    this.coomerProfileData = data;
                    
                    // Auto-select all videos if not SKIP
                    if(data.badge !== 'SKIP') {
                        this.coomerSelectedVideos = data.videos.map(v => v.url);
                        this.showToast(`Nájdených ${data.videos.length} videí pre ${data.creator} (Rating: ${data.rating}, Badge: ${data.badge})`, 'check_circle', 'success');
                    } else {
                        this.showToast(`Profile ${data.creator} má rating ${data.rating} - SKIP`, 'warning', 'warning');
                    }
                } else {
                    this.showToast(data.error || 'Chyba pri skenovaní profilu', 'error', 'error');
                }
            } catch(e) {
                this.showToast('Chyba pri skenovaní profilu: ' + e.message, 'error', 'error');
            } finally {
                this.isScanningCoomer = false;
            }
        },
        toggleCoomerVideo(url) {
            if(this.coomerSelectedVideos.includes(url)) {
                this.coomerSelectedVideos = this.coomerSelectedVideos.filter(u => u !== url);
            } else {
                this.coomerSelectedVideos.push(url);
            }
        },
        async importCoomer() {
            if(!this.coomerProfileData || this.coomerProfileData.badge === 'SKIP') {
                this.showToast('Tento profil nemôže byť importovaný (SKIP)', 'error', 'error');
                return;
            }
            
            if(this.coomerSelectedVideos.length === 0) {
                this.showToast('Vyber aspoň jedno video!', 'error', 'error');
                return;
            }
            
            this.importProgress.active = true;
            this.importProgress.total = this.coomerSelectedVideos.length;
            this.importProgress.done = 0;
            this.importProgress.percent = 0;
            this.showToast(`Importujem ${this.coomerSelectedVideos.length} videí z Coomer...`, 'cloud_download');
            
            try {
                const res = await fetch('/api/import/coomer/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        urls: this.coomerSelectedVideos,
                        batch_name: `Coomer - ${this.coomerProfileData.creator}`,
                        import_speed: this.settings.importSpeed || 'default'
                    })
                });
                const data = await res.json();
                
                if(res.ok) {
                    this.showToast(`Pridaných ${data.count} videí z Coomer!`, 'check_circle', 'success');
                    this.showCoomerModal = false;
                    this.coomerUrl = '';
                    this.coomerProfileData = null;
                    this.coomerSelectedVideos = [];
                    this.loadBatches();
                    this.loadVideos(true);
                } else {
                    this.showToast(data.error || 'Chyba pri importe', 'error', 'error');
                }
            } catch(e) {
                this.showToast('Chyba pri importe: ' + e.message, 'error', 'error');
            } finally {
                this.importProgress.percent = 100;
                setTimeout(() => { this.importProgress.active = false; }, 800);
            }
        },
        onTimeUpdate(e, v) { if(v && Math.random()>0.95) fetch(`/api/videos/${v.id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({resume_time:e.target.currentTime})}); },
        startPreview(v) { if(v.status==='ready' && !this.batchMode) { this.hoverVideoId = v.id; } },
        stopPreview() { this.hoverVideoId = null; },
        isSelected(id) { return this.selectedIds.includes(id); },
        // FIX: Generovanie naozaj unikátneho ID pre toast
        showToast(m, i, t='info') { 
            const id = Date.now().toString(36) + Math.random().toString(36).substr(2);
            this.toasts.push({id, message:m, icon:i, type:t}); 
            setTimeout(()=>this.toasts=this.toasts.filter(x=>x.id!==id), 3000); 
        },
        formatDuration(s) { if(!s) return '0:00'; const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sc=Math.floor(s%60); return h>0?`${h}:${m.toString().padStart(2,'0')}:${sc.toString().padStart(2,'0')}`:`${m}:${sc.toString().padStart(2,'0')}`; },
        getQuality(h) { return h >= 2160 ? '4K' : h >= 1440 ? '1440p' : h >= 1080 ? '1080p' : h >= 720 ? '720p' : 'SD'; },
        getQualityClass(h) { return h >= 2160 ? 'q-4k' : h >= 1080 ? 'q-fhd' : ''; },
        getQualityTitle(w,h) { return (w && h) ? `${w}x${h}` : 'Resolution not available'; },
        getStatusClass(s) { return `status-${s}`; },
        isNew(d) { return (new Date() - new Date(d)) < 86400000; }, 
        toggleFullscreen() { const el = document.querySelector('.player-container'); if(!document.fullscreenElement) el.requestFullscreen(); else document.exitFullscreen(); },
        async togglePip() {
            const video = this.activePlayerIdx === 0 ? this.$refs.videoPlayer1 : this.$refs.videoPlayer2;
            if (!video || !document.pictureInPictureEnabled) {
                this.showToast('PiP not supported', 'error', 'error');
                return;
            }
            try {
                if (document.pictureInPictureElement) {
                    await document.exitPictureInPicture();
                } else {
                    await video.requestPictureInPicture();
                }
            } catch (err) {
                this.showToast('PiP mode failed', 'error', 'error');
                console.error('PiP Error:', err);
            }
        },
    }
}

// 2. Registrácia komponentu pre Alpine
document.addEventListener('alpine:init', () => {
    Alpine.data('dashboard', vipDashboard);
});