/**
 * Pista Libre — Controlador de Sala de Ensayo Multitrack (Howler.js + Alpine.js)
 *
 * Responsabilidad:
 * - Orquestar la carga y reproducción sincronizada de las 4 pistas (vocals, drums, bass, other)
 *   sobre el grafo Web Audio API gestionado por Howler.js.
 * - Aplicar en tiempo real los estados de mezcla (Mute, Solo, Ganancia por canal y Volumen Máster).
 * - Exponer un estado reactivo limpio para Alpine.js y degradar con elegancia a modo visual
 *   cuando una sala de demostración no tenga archivos físicos en disco.
 */

const STEM_KEYS = ["vocals", "drums", "bass", "other"];
const MAX_DRIFT_SECONDS = 0.035; // 35ms umbral máximo de desfase permitido entre pistas

function parseDurationToSeconds(durationStr) {
    if (!durationStr || typeof durationStr !== "string" || !durationStr.includes(":")) {
        return 225;
    }
    const parts = durationStr.split(":");
    const mins = parseInt(parts[0], 10);
    const secs = parseInt(parts[1], 10);
    if (Number.isNaN(mins) || Number.isNaN(secs)) {
        return 225;
    }
    return (mins * 60) + secs;
}

function formatSecondsToMMSS(totalSeconds) {
    const safeSecs = Math.max(0, Math.floor(totalSeconds || 0));
    const mins = Math.floor(safeSecs / 60);
    const secs = safeSecs % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

window.practiceRoom = function (passedConfig = null) {
    let songConfig = passedConfig;
    if (!songConfig || Object.keys(songConfig).length === 0) {
        const dataEl = document.getElementById("song-config-data");
        if (dataEl && dataEl.textContent) {
            try {
                songConfig = JSON.parse(dataEl.textContent);
            } catch (e) {
                songConfig = {};
            }
        } else {
            songConfig = {};
        }
    }

    const initialDuration = parseDurationToSeconds(songConfig.duration);
    const realStemsMap = songConfig.real_stems || {};
    const stemsUrls = songConfig.stems || {};
    const hasAnyRealFile = STEM_KEYS.some((key) => Boolean(realStemsMap[key] && stemsUrls[key]));

    return {
        isPlaying: false,
        isLoading: hasAnyRealFile,
        hasRealAudio: hasAnyRealFile,
        loadedCount: 0,
        expectedCount: 0,
        timer: null,
        howls: {},
        masterKey: null,
        currentTimeSeconds: 0,
        totalDurationSeconds: initialDuration,
        playbackProgress: 0,
        masterVolume: 85,
        tracks: {
            vocals: { muted: false, solo: false, volume: 80 },
            drums: { muted: false, solo: false, volume: 75 },
            bass: { muted: false, solo: false, volume: 70 },
            other: { muted: false, solo: false, volume: 65 },
        },

        init() {
            if (this.hasRealAudio && typeof Howl !== "undefined") {
                this.initHowlerEngine();
            } else {
                this.hasRealAudio = false;
                this.isLoading = false;
            }

            // Atajo de barra espaciadora para Play/Pause rápido con instrumento en mano
            this._keyHandler = (e) => {
                if (e.code !== "Space") return;
                const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : "";
                if (tag === "input" || tag === "button" || tag === "textarea" || tag === "a") return;
                e.preventDefault();
                this.togglePlay();
            };
            window.addEventListener("keydown", this._keyHandler);

            // Liberar recursos de audio al salir de la sala
            this._unloadHandler = () => this.cleanupAudio();
            window.addEventListener("pagehide", this._unloadHandler);
        },

        initHowlerEngine() {
            const activeKeys = STEM_KEYS.filter((k) => Boolean(realStemsMap[k] && stemsUrls[k]));
            this.expectedCount = activeKeys.length;
            this.masterKey = activeKeys[0] || null;

            Howler.volume(this.masterVolume / 100);

            activeKeys.forEach((key) => {
                const howl = new Howl({
                    src: [stemsUrls[key]],
                    preload: true,
                    onload: () => {
                        this.loadedCount += 1;
                        const dur = howl.duration();
                        if (dur && dur > 0) {
                            this.totalDurationSeconds = Math.round(dur);
                        }
                        if (this.loadedCount >= this.expectedCount) {
                            this.isLoading = false;
                            this.applyMixState();
                        }
                    },
                    onloaderror: () => {
                        this.loadedCount += 1;
                        if (this.loadedCount >= this.expectedCount) {
                            this.isLoading = false;
                        }
                    },
                    onend: () => {
                        if (key === this.masterKey) {
                            this.isPlaying = false;
                            this.stopTimer();
                            this.currentTimeSeconds = this.totalDurationSeconds;
                            this.playbackProgress = 100;
                        }
                    },
                });
                this.howls[key] = howl;
            });
        },

        hasAnySolo() {
            return Object.values(this.tracks).some((t) => t.solo);
        },

        isTrackDimmed(key) {
            const track = this.tracks[key];
            if (!track) return false;
            if (track.muted) return true;
            if (this.hasAnySolo() && !track.solo) return true;
            return false;
        },

        applyMixState() {
            if (!this.hasRealAudio || typeof Howler === "undefined") return;

            Howler.volume(this.masterVolume / 100);
            const anySolo = this.hasAnySolo();

            STEM_KEYS.forEach((key) => {
                const track = this.tracks[key];
                const howl = this.howls[key];
                if (!track || !howl) return;

                const isAudible = !track.muted && (!anySolo || track.solo);
                howl.mute(!isAudible);
                howl.volume(track.volume / 100);
            });
        },

        updateTrackVolume(key) {
            if (!this.tracks[key]) return;
            this.applyMixState();
        },

        toggleMute(key) {
            this.tracks[key].muted = !this.tracks[key].muted;
            if (this.tracks[key].muted) {
                this.tracks[key].solo = false;
            }
            this.applyMixState();
        },

        toggleSolo(key) {
            this.tracks[key].solo = !this.tracks[key].solo;
            if (this.tracks[key].solo) {
                this.tracks[key].muted = false;
            }
            this.applyMixState();
        },

        resetAllTracks() {
            STEM_KEYS.forEach((key) => {
                this.tracks[key].muted = false;
                this.tracks[key].solo = false;
            });
            this.applyMixState();
        },

        togglePlay() {
            if (this.isLoading) return;

            if (this.currentTimeSeconds >= this.totalDurationSeconds) {
                this.seekTo(0);
            }

            this.isPlaying = !this.isPlaying;

            if (this.hasRealAudio) {
                if (this.isPlaying) {
                    this.applyMixState();
                    const targetTime = this.currentTimeSeconds;
                    STEM_KEYS.forEach((key) => {
                        const howl = this.howls[key];
                        if (howl) {
                            howl.seek(targetTime);
                            if (!howl.playing()) {
                                howl.play();
                            }
                        }
                    });
                    this.startTimer();
                } else {
                    STEM_KEYS.forEach((key) => {
                        if (this.howls[key]) {
                            this.howls[key].pause();
                        }
                    });
                    this.stopTimer();
                }
            } else {
                if (this.isPlaying) {
                    this.startTimer();
                } else {
                    this.stopTimer();
                }
            }
        },

        restart() {
            this.seekTo(0);
        },

        seekTo(progressVal) {
            const clampedProgress = Math.max(0, Math.min(100, parseFloat(progressVal) || 0));
            this.playbackProgress = clampedProgress;
            this.currentTimeSeconds = (clampedProgress / 100) * this.totalDurationSeconds;

            if (this.hasRealAudio) {
                STEM_KEYS.forEach((key) => {
                    if (this.howls[key]) {
                        this.howls[key].seek(this.currentTimeSeconds);
                    }
                });
            }
        },

        seekFromWaveform(event) {
            const container = event.currentTarget;
            if (!container) return;
            const rect = container.getBoundingClientRect();
            if (!rect.width) return;
            const clickX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
            const percentage = (clickX / rect.width) * 100;
            this.seekTo(percentage);
        },

        startTimer() {
            this.stopTimer();

            if (this.hasRealAudio) {
                this.timer = setInterval(() => {
                    const master = this.howls[this.masterKey];
                    if (!master || !this.isPlaying) return;

                    const pos = master.seek();
                    if (typeof pos === "number") {
                        this.currentTimeSeconds = pos;
                        this.playbackProgress = this.totalDurationSeconds > 0
                            ? Math.min(100, (pos / this.totalDurationSeconds) * 100)
                            : 0;

                        // Corrección automática de deriva (drift) entre pistas
                        STEM_KEYS.forEach((key) => {
                            if (key === this.masterKey) return;
                            const secondary = this.howls[key];
                            if (!secondary || !secondary.playing()) return;
                            const secPos = secondary.seek();
                            if (typeof secPos === "number" && Math.abs(secPos - pos) > MAX_DRIFT_SECONDS) {
                                secondary.seek(pos);
                            }
                        });
                    }
                }, 100);
            } else {
                this.timer = setInterval(() => {
                    if (this.currentTimeSeconds < this.totalDurationSeconds) {
                        this.currentTimeSeconds += 1;
                        this.playbackProgress = Math.min(
                            100,
                            (this.currentTimeSeconds / this.totalDurationSeconds) * 100
                        );
                    } else {
                        this.isPlaying = false;
                        this.stopTimer();
                    }
                }, 1000);
            }
        },

        stopTimer() {
            if (this.timer) {
                clearInterval(this.timer);
                this.timer = null;
            }
        },

        cleanupAudio() {
            this.stopTimer();
            STEM_KEYS.forEach((key) => {
                if (this.howls[key]) {
                    this.howls[key].stop();
                    this.howls[key].unload();
                }
            });
        },

        get currentTimeDisplay() {
            return formatSecondsToMMSS(this.currentTimeSeconds);
        },

        get totalTimeDisplay() {
            return formatSecondsToMMSS(this.totalDurationSeconds);
        },
    };
};
