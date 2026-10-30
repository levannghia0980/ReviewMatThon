import React, { useState, useEffect, useRef, useMemo } from 'react';
import { 
  Zap, Play, Pause, Download, Sparkles, Volume2, VolumeX, Film, CheckCircle2, 
  RefreshCw, RotateCcw, RotateCw, Terminal, Eye, Sliders, Music, Clock, FileText, ArrowRight, Plus, Minus, Trash2,
  Maximize2, ArrowUp, ArrowDown, MoveVertical, Search, Headphones, Clipboard, Link as LinkIcon, ExternalLink
} from 'lucide-react';
import { AIREAD_GENRES, DEFAULT_GENRE } from '../constants/genres';
import { ALL_VOICES } from '../constants/voices';
import { startSafePolling } from '../utils/safePolling';

export default function MainStudioView({ onNavigateTab }) {
  const [url, setUrl] = useState('');
  const [isDownloading, setIsDownloading] = useState(false);
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [videoSearch, setVideoSearch] = useState('');
  const [videoTimestamp, setVideoTimestamp] = useState(Date.now());

  const handlePasteFromClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setUrl(text.trim());
      }
    } catch (e) {
      console.warn("Could not read clipboard:", e);
    }
  };

  // Video Mask & Placement Configuration (Mặc định Bật Che Phụ Đề Gốc để chống đè chữ)
  const [hasMask, setHasMask] = useState(true);
  const [noSub, setNoSub] = useState(false); // Chế độ không sub: không chèn sub, không che dải mờ, chỉ crop & lồng tiếng
  const [maskTop, setMaskTop] = useState(80); // % from top
  const [maskLeft, setMaskLeft] = useState(0); // % left
  const [maskWidth, setMaskWidth] = useState(100); // Giữ để gửi backend
  const [maskHeight, setMaskHeight] = useState(8); // % height cắt sub đáy
  const [topCutPercent, setTopCutPercent] = useState(0); // % height cắt trên đỉnh
  const [blurHeight, setBlurHeight] = useState(13); // % height dải mờ che sub cũ (tùy chỉnh cao nền)
  const [cropRatio, setCropRatio] = useState('16:9'); // '16:9' (YouTube Ngang Chuẩn) | '9:16' (Shorts/TikTok)
  const [backdropOpacity, setBackdropOpacity] = useState('CC'); // 80% opacity mặc định khi bật che

  // Subtitle Vertical Placement, Font Size & Box Padding (Nền sub che chữ gốc)
  const [subBottomOffset, setSubBottomOffset] = useState(2); // % from bottom sát mép cắt đáy (2%)
  const [subFontSize, setSubFontSize] = useState(13); // Cỡ chữ phụ đề vừa vặn theo khung hình bị cắt
  const [subBoxPadding, setSubBoxPadding] = useState(8); // Độ to / padding theo chiều dọc của nền phụ đề (px)

  // Interactive Drag & Resize State
  const [interactionMode, setInteractionMode] = useState(null); // 'move' | 'sub-move' | 'resize-nw' | 'resize-ne' | 'resize-se' | 'resize-sw' | 'resize-n' | 'resize-s' | 'resize-w' | 'resize-e' | 'draw'
  const [dragStart, setDragStart] = useState({ mouseX: 0, mouseY: 0, boxX: 0, boxY: 0, boxW: 0, boxH: 0 });

  // Custom Video Player Controls State
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [videoRatio, setVideoRatio] = useState('16 / 9');

  // Voice & Genre
  const [genre, setGenre] = useState(DEFAULT_GENRE);
  const [voiceCode, setVoiceCode] = useState('BV074_streaming');
  const [isPlayingSample, setIsPlayingSample] = useState(false);

  // Automation Task State
  const [isRunning, setIsRunning] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [progress, setProgress] = useState(0);
  const [currentStep, setCurrentStep] = useState(1);
  const [logs, setLogs] = useState([]);
  const [result, setResult] = useState(null);
  const [videoViewMode, setVideoViewMode] = useState('final');
  const [batchSize, setBatchSize] = useState(300);
  const [downloadQuality, setDownloadQuality] = useState('480p');

  const containerRef = useRef(null);
  const videoRef = useRef(null);

  const handleOpenFolder = async (pId) => {
    try {
      const res = await fetch(`/api/v1/projects/${pId || selectedProject?.id}/open-folder`, { method: 'POST' });
      if (res.ok) {
        // Folder opened
      }
    } catch (err) {
      console.error('Lỗi mở thư mục:', err);
    }
  };

  const handleDeleteProject = async (e, pId, pTitle) => {
    if (e) e.stopPropagation();
    const targetId = pId || selectedProject?.id;
    const targetTitle = pTitle || selectedProject?.title || targetId;
    if (!targetId) return;

    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN XÓA HOÀN TOÀN VIDEO NÀY?\n\n"${targetTitle}" (ID: #${targetId})\n\nThao tác này sẽ xóa sạch 100% video gốc, audio tách, sub dịch, file lồng tiếng và bản render cuối cùng khỏi ổ đĩa!`)) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/projects/${targetId}`, { method: 'DELETE' });
      const data = await res.json();
      if (res.ok) {
        if (selectedProject?.id === targetId) {
          setSelectedProject(null);
        }
        loadProjects();
        alert(data.message || `Đã xóa video #${targetId} và toàn bộ file liên quan thành công!`);
      } else {
        alert(`Lỗi khi xóa: ${data.detail || 'Không thể xóa video'}`);
      }
    } catch (err) {
      alert(`Lỗi mạng: ${err.message}`);
    }
  };

  const handleResetTranslation = async (e, pId, pTitle) => {
    if (e) e.stopPropagation();
    const targetId = pId || selectedProject?.id;
    const targetTitle = pTitle || selectedProject?.title || targetId;
    if (!targetId) return;

    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET BẢN DỊCH?\n\n"${targetTitle}" (ID: #${targetId})\n\nThao tác này sẽ:\n✓ GIỮ NGUYÊN video gốc & toàn bộ câu thoại gốc tiếng Trung ASR (không phải gửi CapCut STT lại)\n✗ XÓA SẠCH 100% bản dịch tiếng Việt, phụ đề và kịch bản cũ\n✗ XÓA SẠCH 100% audio thuyết minh TTS và video render thành phẩm\n\nDự án sẽ sẵn sàng để Dịch lại từ đầu bằng AI LLM!`)) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/projects/${targetId}/reset-translation`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setVideoTimestamp(Date.now());
        await loadProjects();
        if (selectedProject?.id === targetId) {
          setSelectedProject(prev => prev ? {
            ...prev,
            status: data.status_code || 'TRANSCRIBED',
            has_final: false,
            final_video_path: null,
            translated_dialogues: 0
          } : null);
          setVideoViewMode('raw');
        }
        alert(data.message || `Đã reset bản dịch video #${targetId} thành công!`);
      } else {
        alert(`Lỗi khi reset: ${data.detail || 'Không thể reset bản dịch'}`);
      }
    } catch (err) {
      alert(`Lỗi mạng: ${err.message}`);
    }
  };

  const handleResetDownloaded = async (e, pId, pTitle) => {
    if (e) e.stopPropagation();
    const targetId = pId || selectedProject?.id;
    const targetTitle = pTitle || selectedProject?.title || targetId;
    if (!targetId) return;

    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET VỀ LÚC MỚI TẢI XONG?\n\n"${targetTitle}" (ID: #${targetId})\n\nThao tác này sẽ:\n✓ GIỮ NGUYÊN DUY NHẤT file video gốc đã tải về\n✗ XÓA SẠCH 100% câu thoại tiếng Trung & bản dịch tiếng Việt\n✗ XÓA SẠCH 100% audio thuyết minh TTS và video render thành phẩm\n\nVideo sẽ trở về trạng thái như vừa tải xong để làm lại từ bước STT!`)) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/projects/${targetId}/reset`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setVideoTimestamp(Date.now());
        await loadProjects();
        if (selectedProject?.id === targetId) {
          setSelectedProject(prev => prev ? {
            ...prev,
            status: data.status_code || 'DOWNLOADED',
            has_final: false,
            final_video_path: null,
            translated_dialogues: 0
          } : null);
          setVideoViewMode('raw');
        }
        alert(data.message || `Đã reset video #${targetId} về lúc mới tải xong thành công!`);
      } else {
        alert(`Lỗi khi reset: ${data.detail || 'Không thể reset video'}`);
      }
    } catch (err) {
      alert(`Lỗi mạng: ${err.message}`);
    }
  };

  useEffect(() => {
    loadProjects();
    loadSettings();
    checkActiveTask(); // Gọi đúng 1 lần khi mở app

    const handleSettingsUpdated = (e) => {
      const cfg = e?.detail;
      if (cfg && cfg.translation_batch_size) {
        setBatchSize(cfg.translation_batch_size);
      } else {
        loadSettings();
      }
    };
    window.addEventListener('settings-updated', handleSettingsUpdated);

    // KẾT NỐI SERVER-SENT EVENTS (SSE): Nhận tín hiệu thời gian thực khi BE có sự kiện
    // Không bao giờ spam polling lặp đi lặp lại!
    let eventSource = null;
    try {
      eventSource = new EventSource('/api/v1/pipeline/events');
      eventSource.onmessage = (e) => {
        try {
          const packet = JSON.parse(e.data);
          if (packet.event === 'log') {
            const { project_id, log } = packet.data || {};
            // Nếu log thuộc về project đang xem thì thêm ngay vào bảng log
            if (log) {
              setLogs(prev => {
                const updated = [...prev, log];
                if (updated.length > 500) updated.shift();
                return updated;
              });
            }
          } else if (packet.event === 'task_update') {
            const task = packet.data || {};
            // CHỈ cập nhật vào Xưởng Video nếu task thuộc về quy trình của Xưởng Video (task_auto_, task_compose_, task_dl_)
            // Bỏ qua task dịch thuật độc lập (task_trans_) từ Thư Viện Thoại để 2 màn hình hoạt động độc lập
            const isStudioTask = task.id && !task.id.startsWith('task_trans_') && !task.id.startsWith('task_stt_');
            if (!isStudioTask && task.id !== taskId) return;

            if (task.project_id) {
              setActiveTasksMap(prev => ({
                ...prev,
                [task.project_id]: task
              }));
            }
            // Nếu là task của project đang xem
            if (task.id === taskId || (isStudioTask && task.project_id && selectedProject?.id === task.project_id)) {
              setProgress(task.progress || 0);
              setCurrentStep(task.step || 1);
              if (['completed', 'DONE', 'success', 'SUCCESS', 'error', 'failed', 'cancelled'].includes(task.status)) {
                setIsRunning(false);
                loadProjects();
              } else if (task.status === 'running') {
                setIsRunning(true);
              }
            }
          } else if (packet.event === 'task_cancelled') {
            setIsRunning(false);
            checkActiveTask();
          }
        } catch (err) {}
      };
      eventSource.onerror = () => {
        // Tự động kết nối lại khi rớt mạng
      };
    } catch (err) {
      console.warn('SSE Error:', err);
    }

    return () => {
      window.removeEventListener('settings-updated', handleSettingsUpdated);
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [taskId, selectedProject?.id]);

  const [activeTasksMap, setActiveTasksMap] = useState({}); // { project_id: task_object }

  const checkActiveTask = async () => {
    try {
      // 1. Quét danh sách tất cả các task đang chạy ngầm trên toàn bộ server (Đa nhiệm)
      const resTasks = await fetch('/api/v1/pipeline/active-tasks');
      if (resTasks.ok) {
        const data = await resTasks.json();
        const map = {};
        (data.tasks || []).forEach(t => {
          // CHỈ lọc các task thuộc quy trình tự động hóa / render video của Studio (task_auto_, task_compose_, task_dl_)
          // Bỏ qua hoàn toàn task dịch lẻ (task_trans_) và bóc tách (task_stt_) của Thư Viện Thoại
          if (t.project_id && t.id && !t.id.startsWith('task_trans_') && !t.id.startsWith('task_stt_')) {
            map[t.project_id] = t;
          }
        });
        setActiveTasksMap(map);

        // Nếu project đang chọn có task của Xưởng Video đang chạy, tự động kết nối
        if (selectedProject && map[selectedProject.id]) {
          const curT = map[selectedProject.id];
          setTaskId(curT.id);
          setIsRunning(true);
          setProgress(curT.progress || 0);
          setCurrentStep(curT.step || 1);
        }
      }

      // 2. Tương thích ngược với active-task đơn
      const res = await fetch('/api/v1/pipeline/active-task');
      if (res.ok) {
        const data = await res.json();
        if (data && data.id && (data.status === 'running' || data.status === 'pending')) {
          if (!data.id.startsWith('task_trans_') && !data.id.startsWith('task_stt_')) {
            if (!selectedProject || (data.meta?.project_id && selectedProject.id === data.meta.project_id)) {
              setTaskId(data.id);
              setIsRunning(true);
              setProgress(data.progress || 0);
              setCurrentStep(data.step || 1);
              if (data.logs && data.logs.length > 0) setLogs(data.logs);
            }
          }
        }
      }
    } catch (e) {
      console.warn('Lỗi kiểm tra active task:', e);
    }
  };

  // Nạp lại log từ Server cho project đang chọn
  const loadProjectServerLogs = async (projectId) => {
    if (!projectId) return;
    try {
      const res = await fetch(`/api/v1/pipeline/projects/${projectId}/logs`);
      if (res.ok) {
        const data = await res.json();
        if (data.logs && data.logs.length > 0) {
          setLogs(data.logs);
        }
      }
    } catch (e) {}
  };

  const loadSettings = async () => {
    try {
      const res = await fetch('/api/v1/settings');
      if (res.ok) {
        const data = await res.json();
        if (data.translation_batch_size) {
          setBatchSize(data.translation_batch_size);
        }
      }
    } catch (e) {}
  };

  const loadProjects = async (selectId = null) => {
    try {
      const res = await fetch(`/api/v1/projects/?page=1&page_size=100&_t=${Date.now()}`, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        const items = data.items || [];
        setProjects(items);
        if (items.length > 0) {
          setSelectedProject(prev => {
            if (selectId) {
              const matched = items.find(p => p.id === selectId);
              if (matched) return matched;
            }
            if (!prev) return items[0];
            const updated = items.find(p => p.id === prev.id);
            return updated || prev;
          });
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Poll Task Status An Toàn
  useEffect(() => {
    if (!isRunning || !taskId) return;

    const stopPolling = startSafePolling({
      interval: 1500,
      timeoutMs: 4000,
      fn: async (signal) => {
        const res = await fetch(`/api/v1/pipeline/status/${taskId}`, { signal });
        if (!res.ok) return true;
        const data = await res.json();
        setProgress(data.progress || 0);
        setCurrentStep(data.step || 1);
        if (data.logs) setLogs(data.logs);

        if (['completed', 'DONE', 'success', 'SUCCESS'].includes(data.status)) {
          setIsRunning(false);
          setResult(data.result);
          if (data.result?.final_video_path) {
            setVideoViewMode('final');
          } else {
            setVideoViewMode('raw');
          }
          loadProjects(data.result?.project_id);
          window.dispatchEvent(new CustomEvent('video-projects-updated'));
          return false;
        } else if (['failed', 'FAILED', 'cancelled', 'CANCELLED', 'error'].includes(data.status)) {
          setIsRunning(false);
          if (data.status === 'failed' || data.status === 'FAILED') {
            alert(`Lỗi: ${data.error}`);
          }
          return false;
        }
        return true;
      }
    });

    return () => stopPolling();
  }, [isRunning, taskId]);

  // Keyboard navigation for Subtitle Up/Down & Play/Pause
  const handleSkip = (seconds) => {
    if (!videoRef.current) return;
    const cur = videoRef.current.currentTime || 0;
    const maxDur = duration || videoRef.current.duration || 100;
    const newTime = Math.max(0, Math.min(maxDur, cur + seconds));
    videoRef.current.currentTime = newTime;
    setCurrentTime(newTime);
  };

  // Keyboard navigation for Subtitle Up/Down, Play/Pause & Seek (-10s / +10s)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.key === 'ArrowUp') {
        if (videoViewMode === 'raw') {
          e.preventDefault();
          setSubBottomOffset(prev => Math.min(85, prev + 1));
        }
      } else if (e.key === 'ArrowDown') {
        if (videoViewMode === 'raw') {
          e.preventDefault();
          setSubBottomOffset(prev => Math.max(1, prev - 1));
        }
      } else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'j') {
        e.preventDefault();
        handleSkip(-10);
      } else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'l') {
        e.preventDefault();
        handleSkip(10);
      } else if (e.key === ' ' || e.key.toLowerCase() === 'k') {
        e.preventDefault();
        togglePlayPause();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isPlaying, videoViewMode, duration]);

  // Video Time Update Listeners
  const handleTimeUpdate = () => {
    if (videoRef.current) {
      setCurrentTime(videoRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      setDuration(videoRef.current.duration);
      if (videoRef.current.videoWidth && videoRef.current.videoHeight) {
        setVideoRatio(`${videoRef.current.videoWidth} / ${videoRef.current.videoHeight}`);
      }
    }
  };

  const togglePlayPause = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      videoRef.current.play();
      setIsPlaying(true);
    } else {
      videoRef.current.pause();
      setIsPlaying(false);
    }
  };

  const handleSeek = (e) => {
    const seekTime = parseFloat(e.target.value);
    setCurrentTime(seekTime);
    if (videoRef.current) {
      videoRef.current.currentTime = seekTime;
    }
  };

  const handleVolumeChange = (e) => {
    const newVol = parseFloat(e.target.value);
    setVolume(newVol);
    setIsMuted(newVol === 0);
    if (videoRef.current) {
      videoRef.current.volume = newVol;
      videoRef.current.muted = newVol === 0;
    }
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    if (isMuted) {
      videoRef.current.muted = false;
      setIsMuted(false);
      if (volume === 0) setVolume(0.5);
    } else {
      videoRef.current.muted = true;
      setIsMuted(true);
    }
  };

  const handleFullscreen = () => {
    if (containerRef.current) {
      if (!document.fullscreenElement) {
        containerRef.current.requestFullscreen().catch(err => console.error(err));
      } else {
        document.exitFullscreen().catch(err => console.error(err));
      }
    }
  };

  // Mouse Drag Engine cho Cắt Sub Đáy và Di chuyển Vị trí Subtitle
  const handleMouseDown = (e, mode) => {
    e.stopPropagation();
    e.preventDefault();
    if (!containerRef.current) return;

    setInteractionMode(mode);
  };

  const handleCanvasMouseDown = (e) => {
    // Không thao tác vẽ tự do
  };

  const handleMouseMove = (e) => {
    if (!interactionMode || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const currentMouseY = Math.max(0, Math.min(100, ((e.clientY - rect.top) / rect.height) * 100));

    if (interactionMode === 'crop-bottom') {
      const newHeight = Math.max(1, Math.min(45, 100 - currentMouseY));
      setMaskHeight(Math.round(newHeight));
    } else if (interactionMode === 'crop-top') {
      const newTop = Math.max(0, Math.min(35, currentMouseY));
      setTopCutPercent(Math.round(newTop));
    } else if (interactionMode === 'sub-move') {
      const rawBottom = 100 - currentMouseY;
      if (hasMask) {
        const newBottom = Math.max(0, Math.min(80, rawBottom - maskHeight));
        setSubBottomOffset(Math.round(newBottom));
      } else {
        const newBottom = Math.max(0, Math.min(80, rawBottom));
        setSubBottomOffset(Math.round(newBottom));
      }
    }
  };

  const handleMouseUp = () => {
    setInteractionMode(null);
  };

  useEffect(() => {
    const onUp = () => setInteractionMode(null);
    const onKeyDown = (e) => {
      if (['INPUT', 'TEXTAREA'].includes(e.target?.tagName)) return;
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSubBottomOffset(prev => Math.min(85, prev + 1));
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSubBottomOffset(prev => Math.max(0, prev - 1));
      }
    };
    window.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [hasMask, maskHeight]);

  // Download video via URL
  const handleDownloadOnly = async () => {
    if (!url.trim()) {
      alert('Vui lòng nhập link video Bilibili hoặc YouTube!');
      return;
    }
    setIsDownloading(true);
    try {
      const res = await fetch('/api/v1/pipeline/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: url.trim(),
          quality: downloadQuality,
          source_language: 'zh',
          extract_sub: false
        })
      });

      if (!res.ok) throw new Error('Không thể bắt đầu tải video');
      const data = await res.json();
      setTaskId(data.task_id);
      setIsRunning(true);
      setUrl('');
    } catch (e) {
      alert(`Lỗi: ${e.message}`);
    } finally {
      setIsDownloading(false);
    }
  };

  // 1-Click Automation Execution
  const handleStartAutomation = async () => {
    if (!selectedProject && !url.trim()) {
      alert('Vui lòng chọn 1 video trong danh sách hoặc nhập link URL video mới!');
      return;
    }

    setIsRunning(true);
    setProgress(5);
    setLogs([]);
    setResult(null);

    try {
      // 🎬 TÍNH TOÁN TỌA ĐỘ DẢI MỜ & PHỤ ĐỀ ĐỒNG BỘ 1:1 VỚI PREVIEW CANVAS
      const calcBlurH = Math.max(6, blurHeight || 13);
      const baseBottom = hasMask ? (maskHeight + subBottomOffset) : subBottomOffset;
      // Đặt chữ phụ đề nằm CHÍNH GIỮA dải mờ: tâm dải mờ cách đáy = baseBottom + (calcBlurH / 2)
      const finalMarginV = Math.round(baseBottom + (calcBlurH * 0.45));
      const calcMaskTop = Math.max(0, Math.min(95, 100 - baseBottom - calcBlurH));
      const calcMaskWidth = Math.min(100, Math.max(30, maskWidth || 100));
      const calcMaskLeft = Math.max(0, (100 - calcMaskWidth) / 2);

      const payload = {
        quality: downloadQuality,
        source_language: 'zh',
        genre: genre,
        voice_code: voiceCode,
        margin_v: Math.max(2, Math.round(subBottomOffset + (calcBlurH * 0.22))), // Căn chính giữa khối chữ vào dải mờ
        backdrop_opacity_hex: backdropOpacity,
        has_mask: hasMask,
        mask_top: calcMaskTop, // % từ đỉnh xuống dải mờ
        mask_left: calcMaskLeft,
        mask_width: calcMaskWidth, // Độ rộng dải mờ %
        mask_height: maskHeight, // Chiều cao cắt sub đáy %
        bottom_cut_percent: maskHeight, // Chiều cao cắt sub đáy %
        top_cut_percent: topCutPercent, // Chiều cao cắt trên đỉnh %
        blur_height: calcBlurH, // Chiều cao dải mờ %
        sub_bottom_offset: subBottomOffset, // Vị trí dải mờ từ đáy khung hình crop lên %
        crop_ratio: cropRatio,
        font_size: subFontSize,
        box_style: 'white_box',
        box_padding: subBoxPadding,
        karaoke_highlight_color: '&H000000FF',
        channel_name: '@Mắt Thần Review',
        channel_opacity: 0.35,
        logo_position: 'top_left',
        logo_size: 120,
        batch_size: batchSize,
        render_subtitles: !noSub
      };

      const trimmedUrl = url.trim();
      if (trimmedUrl) {
        payload.url = trimmedUrl;
      } else if (selectedProject) {
        payload.project_id = selectedProject.id;
      } else {
        alert("Vui lòng nhập URL video hoặc chọn một dự án từ danh sách!");
        setIsRunning(false);
        return;
      }

      const res = await fetch('/api/v1/pipeline/full-auto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) throw new Error('Không thể kích hoạt quy trình tự động');
      const data = await res.json();
      setTaskId(data.task_id);
    } catch (e) {
      setIsRunning(false);
      alert(`Lỗi: ${e.message}`);
    }
  };

  const handleCancelAutomation = async () => {
    if (!taskId) {
      setIsRunning(false);
      return;
    }
    try {
      await fetch(`/api/v1/pipeline/cancel/${taskId}`, { method: 'POST' });
      setIsRunning(false);
      setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: '🛑 Đã dừng tiến trình! Dữ liệu các lô đã dịch xong được bảo toàn nguyên vẹn.', type: 'amber' }]);
      loadProjects();
    } catch (err) {
      console.error('Lỗi khi hủy tiến trình:', err);
      setIsRunning(false);
    }
  };

  // Test TikTok voice sample
  const handleTestVoiceSample = async () => {
    setIsPlayingSample(true);
    try {
      const text = genre === 'cophong' 
        ? "Đạo hữu xin dừng bước! Hãy cùng theo dõi trận chiến kinh thiên động địa ngay sau đây."
        : "Chào mừng các bạn đã quay trở lại với Mắt Thần Review, diễn biến câu chuyện hôm nay vô cùng gay cấn.";

      const res = await fetch('/api/v1/tts/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: text,
          voice_code: voiceCode,
          apply_mastering: true,
          playback_speed: 1.0
        })
      });

      if (!res.ok) throw new Error('Không thể tạo âm thanh');
      const data = await res.json();
      if (data.audio_base64) {
        const audio = new Audio(`data:audio/mp3;base64,${data.audio_base64}`);
        audio.onended = () => setIsPlayingSample(false);
        audio.play();
      }
    } catch (e) {
      setIsPlayingSample(false);
      alert(`Lỗi nghe thử giọng: ${e.message}`);
    }
  };

  const getStatusBadge = (p) => {
    if (p.has_final || p.status === 'COMPLETED') {
      return (
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '3px',
          fontSize: '10px',
          fontWeight: 700,
          color: '#059669',
          background: '#ecfdf5',
          border: '1px solid #a7f3d0',
          padding: '1px 6px',
          borderRadius: '10px',
          lineHeight: 1.2
        }}>
          <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#10b981' }} />
          Đã Xong
        </span>
      );
    }
    if (p.translated_dialogues > 0 || p.status === 'TRANSLATED') {
      return (
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '3px',
          fontSize: '10px',
          fontWeight: 700,
          color: '#7c3aed',
          background: '#f5f3ff',
          border: '1px solid #ddd6fe',
          padding: '1px 6px',
          borderRadius: '10px',
          lineHeight: 1.2
        }}>
          <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#8b5cf6' }} />
          Đã Dịch
        </span>
      );
    }
    return (
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '3px',
        fontSize: '10px',
        fontWeight: 700,
        color: '#d97706',
        background: '#fffbeb',
        border: '1px solid #fde68a',
        padding: '1px 6px',
        borderRadius: '10px',
        lineHeight: 1.2
      }}>
        <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#f59e0b' }} />
        Mới Tải
      </span>
    );
  };

  const formatDuration = (sec) => {
    if (!sec) return '00:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const filteredProjects = useMemo(() => {
    if (!videoSearch.trim()) return projects;
    const s = videoSearch.toLowerCase();
    return projects.filter(p => (p.title || '').toLowerCase().includes(s) || (p.video_id || '').toLowerCase().includes(s));
  }, [projects, videoSearch]);

  const parseVideoRatio = () => {
    if (videoRatio && videoRatio.includes('/')) {
      const parts = videoRatio.split('/').map(v => parseFloat(v.trim()));
      if (parts.length === 2 && parts[0] > 0 && parts[1] > 0) return parts[0] / parts[1];
    }
    return 16 / 9;
  };

  const videoAspect = parseVideoRatio();
  const remainH = Math.max(10, 100 - maskHeight - topCutPercent);
  // Tỷ lệ khung hình: YouTube Ngang chuẩn là 16/9, Shorts/TikTok là 9/16
  const ratioVal = cropRatio === '9:16' ? (9 / 16) : (16 / 9);
  // targetW tính theo % container
  const targetW = Math.min(100, Math.max(15, (remainH / videoAspect) * ratioVal));
  const targetLeft = Math.max(0, (100 - targetW) / 2);
  const targetRight = targetLeft + targetW;

  return (
    <div style={{
      width: '100%',
      maxWidth: '1480px',
      margin: '0 auto',
      padding: '10px 16px',
      height: '100%',
      boxSizing: 'border-box',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden'
    }}>
      
      {/* 2-COLUMN STUDIO WORKSPACE */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1.38fr 1fr',
        gap: '12px',
        alignItems: 'stretch',
        height: '100%',
        minHeight: 0,
        minWidth: 0,
        flex: 1,
        overflow: 'hidden'
      }}>
        
        {/* LEFT COLUMN: VIDEO CANVAS CARD + PLAYBACK + TIẾN TRÌNH XỬ LÝ (PROGRESS CARD) */}
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
          height: '100%',
          minHeight: 0,
          minWidth: 0,
          overflowY: 'auto',
          paddingRight: '4px'
        }}>
          
          {/* 1. VIDEO CANVAS CARD (CHUẨN TỈ LỆ YOUTUBE 16:9 CHÍNH XÁC TỪNG PIXEL) */}
          <div style={{
            background: '#ffffff',
            border: '1.5px solid #cbd5e1',
            borderRadius: '10px',
            padding: '10px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)',
            flexShrink: 0
          }}>
            
            {/* Row 1: Sub-header: Title, Mode Toggles & Project Actions */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <div style={{ fontSize: '13.5px', fontWeight: 800, display: 'flex', alignItems: 'center', gap: '6px', color: '#0f172a' }}>
                <Film size={16} color="#2563eb" /> Khung Video & Phụ Đề
              </div>

              {/* Action Buttons: Open Folder, Reset, Delete */}
              {selectedProject && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  {(selectedProject?.has_final || selectedProject?.status === 'COMPLETED') && (
                    <button
                      type="button"
                      onClick={() => handleOpenFolder(selectedProject.id)}
                      style={{
                        padding: '3px 8px',
                        fontSize: '11px',
                        fontWeight: 700,
                        borderRadius: '4px',
                        cursor: 'pointer',
                        background: '#10b981',
                        color: '#ffffff',
                        border: '1px solid #059669',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '3px'
                      }}
                      title="Mở thư mục output/final_videos trong Windows"
                    >
                      📂 Thư Mục File
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={(e) => handleResetTranslation(e, selectedProject.id, selectedProject.title)}
                    style={{
                      padding: '3px 8px',
                      fontSize: '11px',
                      fontWeight: 800,
                      borderRadius: '4px',
                      cursor: 'pointer',
                      background: '#fffbeb',
                      color: '#d97706',
                      border: '1.5px solid #f59e0b',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px'
                    }}
                    title="Reset Bản Dịch: Xóa sạch bản dịch tiếng Việt, audio và video render để dịch lại mới tinh; giữ nguyên câu thoại gốc tiếng Trung ASR để không phải gửi CapCut STT lại"
                  >
                    <RotateCcw size={11} /> Reset Bản Dịch
                  </button>

                  <button
                    type="button"
                    onClick={(e) => handleResetDownloaded(e, selectedProject.id, selectedProject.title)}
                    style={{
                      padding: '3px 8px',
                      fontSize: '11px',
                      fontWeight: 800,
                      borderRadius: '4px',
                      cursor: 'pointer',
                      background: '#fef2f2',
                      color: '#b91c1c',
                      border: '1.5px solid #ef4444',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px'
                    }}
                    title="Reset Về Mới Tải: Xóa sạch toàn bộ câu thoại, bản dịch, audio và video; chỉ giữ nguyên video thô vừa tải về"
                  >
                    <RefreshCw size={11} /> Reset Về Mới Tải
                  </button>

                  <button
                    type="button"
                    onClick={(e) => handleDeleteProject(e, selectedProject.id, selectedProject.title)}
                    style={{
                      padding: '3px 8px',
                      fontSize: '11px',
                      fontWeight: 700,
                      borderRadius: '4px',
                      cursor: 'pointer',
                      background: '#fee2e2',
                      color: '#dc2626',
                      border: '1px solid #fca5a5',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px'
                    }}
                    title="Xóa video này"
                  >
                    <Trash2 size={11} /> Xóa
                  </button>
                </div>
              )}
            </div>

            {/* Row 2: Bộ Điều Khiển Phụ Đề & Khung Hình (Grid 4 Cột Dàn Đều 100%, Nút Tĩnh Kích Thước) */}
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              background: '#f8fafc',
              padding: '10px 14px',
              borderRadius: '8px',
              border: '1px solid #e2e8f0',
              width: '100%',
              boxSizing: 'border-box'
            }}>
              {/* HÀNG 1: CẤU HÌNH PHỤ ĐỀ (DÀN ĐỀU 4 CỘT) */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: '10px',
                width: '100%',
                alignItems: 'center'
              }}>
                {/* Cột 1: Cao Độ Sub */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  background: '#ffffff',
                  padding: '4px 8px',
                  borderRadius: '6px',
                  border: '1px solid #cbd5e1',
                  height: '34px',
                  boxSizing: 'border-box',
                  opacity: noSub ? 0.4 : 1,
                  pointerEvents: noSub ? 'none' : 'auto',
                  transition: 'opacity 0.15s'
                }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', whiteSpace: 'nowrap' }}>Cao Độ Sub:</span>
                  <button
                    type="button"
                    onClick={() => setSubBottomOffset(prev => Math.max(0, prev - 1))}
                    title="Hạ phụ đề xuống (Phím ↓)"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#f1f5f9',
                      border: '1px solid #cbd5e1',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#2563eb'
                    }}
                  >
                    <ArrowDown size={13} />
                  </button>
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
                    <input
                      type="number"
                      min="0"
                      max="85"
                      value={subBottomOffset}
                      onChange={(e) => setSubBottomOffset(Math.max(0, Math.min(85, parseInt(e.target.value, 10) || 0)))}
                      style={{
                        width: '36px',
                        height: '22px',
                        border: '1.5px solid #2563eb',
                        borderRadius: '4px',
                        fontSize: '12px',
                        fontWeight: 800,
                        textAlign: 'center',
                        color: '#0f172a'
                      }}
                      title="Nhập trực tiếp % khoảng cách từ đáy (0% - 85%)"
                    />
                    <span style={{ fontSize: '12px', fontWeight: 800, color: '#0f172a' }}>%</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSubBottomOffset(prev => Math.min(85, prev + 1))}
                    title="Nâng phụ đề lên (Phím ↑)"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#f1f5f9',
                      border: '1px solid #cbd5e1',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#2563eb'
                    }}
                  >
                    <ArrowUp size={13} />
                  </button>
                </div>

                {/* Cột 2: Cỡ Chữ */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  background: '#ffffff',
                  padding: '4px 8px',
                  borderRadius: '6px',
                  border: '1px solid #cbd5e1',
                  height: '34px',
                  boxSizing: 'border-box',
                  opacity: noSub ? 0.4 : 1,
                  pointerEvents: noSub ? 'none' : 'auto',
                  transition: 'opacity 0.15s'
                }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', whiteSpace: 'nowrap' }}>Cỡ Chữ:</span>
                  <button
                    type="button"
                    onClick={() => setSubFontSize(prev => Math.max(9, prev - 1))}
                    title="Giảm cỡ chữ"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#f1f5f9',
                      border: '1px solid #cbd5e1',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#2563eb'
                    }}
                  >
                    <Minus size={13} />
                  </button>
                  <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', fontWeight: 800, color: '#0f172a', minWidth: '34px', textAlign: 'center' }}>
                    {subFontSize}px
                  </span>
                  <button
                    type="button"
                    onClick={() => setSubFontSize(prev => Math.min(24, prev + 1))}
                    title="Tăng cỡ chữ"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#f1f5f9',
                      border: '1px solid #cbd5e1',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#2563eb'
                    }}
                  >
                    <Plus size={13} />
                  </button>
                </div>

                {/* Cột 3: Cao Nền */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  background: '#eff6ff',
                  padding: '4px 8px',
                  borderRadius: '6px',
                  border: '1.5px solid #2563eb',
                  height: '34px',
                  boxSizing: 'border-box',
                  opacity: noSub ? 0.4 : 1,
                  pointerEvents: noSub ? 'none' : 'auto',
                  transition: 'opacity 0.15s'
                }}>
                  <span style={{ fontSize: '12px', fontWeight: 800, color: '#1e40af', whiteSpace: 'nowrap' }}>Cao Nền:</span>
                  <button
                    type="button"
                    onClick={() => setBlurHeight(prev => Math.max(8, prev - 1))}
                    title="Giảm chiều cao dải mờ che chữ"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#ffffff',
                      border: '1px solid #93c5fd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#1d4ed8'
                    }}
                  >
                    <Minus size={13} />
                  </button>
                  <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', fontWeight: 900, color: '#1d4ed8', minWidth: '34px', textAlign: 'center' }}>
                    {blurHeight}%
                  </span>
                  <button
                    type="button"
                    onClick={() => setBlurHeight(prev => Math.min(30, prev + 1))}
                    title="Tăng chiều cao dải mờ che chữ"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#ffffff',
                      border: '1px solid #93c5fd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#1d4ed8'
                    }}
                  >
                    <Plus size={13} />
                  </button>
                </div>

                {/* Cột 4 (BÊN PHẢI CÙNG): Nút Toggle Có Sub ⇄ Không Sub (Kích Thước Tĩnh 100%, Căn Giữa) */}
                <button
                  type="button"
                  onClick={() => setNoSub(prev => !prev)}
                  style={{
                    width: '100%',
                    height: '34px',
                    fontSize: '12.5px',
                    fontWeight: 800,
                    borderRadius: '6px',
                    cursor: 'pointer',
                    border: !noSub ? '1.5px solid #2563eb' : '1.5px solid #d97706',
                    background: !noSub ? '#2563eb' : '#fffbeb',
                    color: !noSub ? '#ffffff' : '#b45309',
                    boxShadow: !noSub ? '0 1px 4px rgba(37,99,235,0.25)' : 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '6px',
                    transition: 'all 0.15s ease',
                    boxSizing: 'border-box'
                  }}
                  title={!noSub ? "Đang BẬT phụ đề (Nhấn để chuyển sang Không Sub)" : "Đang TẮT phụ đề (Nhấn để Bật phụ đề)"}
                >
                  <span>{!noSub ? '💬 Có Sub' : '⚡ Không Sub'}</span>
                </button>
              </div>

              {/* HÀNG 2: CẮT KHUNG HÌNH, TỶ LỆ & XEM/LÀM (DÀN ĐỀU 4 CỘT) */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: '10px',
                width: '100%',
                alignItems: 'center',
                paddingTop: '6px',
                borderTop: '1px dashed #cbd5e1'
              }}>
                {/* Cột 1: Cắt Mép Trên */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  background: '#eff6ff',
                  padding: '4px 8px',
                  borderRadius: '6px',
                  border: '1px solid #93c5fd',
                  height: '34px',
                  boxSizing: 'border-box'
                }}>
                  <span style={{ fontSize: '12px', fontWeight: 800, color: '#1e40af', whiteSpace: 'nowrap' }}>✂ Cắt Trên:</span>
                  <button
                    type="button"
                    onClick={() => setTopCutPercent(prev => Math.max(0, prev - 1))}
                    title="Giảm độ cao cắt trên (về 0% là không cắt)"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#ffffff',
                      border: '1px solid #93c5fd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#1e40af',
                      fontWeight: 800
                    }}
                  >
                    <Minus size={13} />
                  </button>
                  <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', fontWeight: 900, color: '#2563eb', minWidth: '32px', textAlign: 'center' }}>
                    {topCutPercent}%
                  </span>
                  <button
                    type="button"
                    onClick={() => setTopCutPercent(prev => Math.min(30, prev + 1))}
                    title="Tăng độ cao cắt trên"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#ffffff',
                      border: '1px solid #93c5fd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#1e40af',
                      fontWeight: 800
                    }}
                  >
                    <Plus size={13} />
                  </button>
                </div>

                {/* Cột 2: Cắt Mép Đáy */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  background: '#eff6ff',
                  padding: '4px 8px',
                  borderRadius: '6px',
                  border: '1px solid #93c5fd',
                  height: '34px',
                  boxSizing: 'border-box'
                }}>
                  <span style={{ fontSize: '12px', fontWeight: 800, color: '#1e40af', whiteSpace: 'nowrap' }}>✂ Cắt Đáy:</span>
                  <button
                    type="button"
                    onClick={() => setMaskHeight(prev => Math.max(0, prev - 1))}
                    title="Giảm độ cao cắt sub đáy (về 0% là không cắt)"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#ffffff',
                      border: '1px solid #93c5fd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#1e40af',
                      fontWeight: 800
                    }}
                  >
                    <Minus size={13} />
                  </button>
                  <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', fontWeight: 900, color: '#2563eb', minWidth: '32px', textAlign: 'center' }}>
                    {maskHeight}%
                  </span>
                  <button
                    type="button"
                    onClick={() => setMaskHeight(prev => Math.min(35, prev + 1))}
                    title="Tăng độ cao cắt sub đáy"
                    style={{
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: '#ffffff',
                      border: '1px solid #93c5fd',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      color: '#1e40af',
                      fontWeight: 800
                    }}
                  >
                    <Plus size={13} />
                  </button>
                </div>

                {/* Cột 3: Nút Toggle Tỷ Lệ 16:9 ⇄ 9:16 (Kích Thước Tĩnh 100%, Căn Giữa) */}
                <button
                  type="button"
                  onClick={() => setCropRatio(prev => prev === '16:9' ? '9:16' : '16:9')}
                  style={{
                    width: '100%',
                    height: '34px',
                    fontSize: '12.5px',
                    fontWeight: 800,
                    borderRadius: '6px',
                    cursor: 'pointer',
                    border: cropRatio === '16:9' ? '1.5px solid #0284c7' : '1.5px solid #7c3aed',
                    background: cropRatio === '16:9' ? '#f0f9ff' : '#f5f3ff',
                    color: cropRatio === '16:9' ? '#0369a1' : '#6d28d9',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '6px',
                    transition: 'all 0.15s ease',
                    boxSizing: 'border-box'
                  }}
                  title={cropRatio === '16:9' ? "Tỷ lệ 16:9 Ngang YouTube (Nhấn để đổi sang 9:16 Dọc Shorts/TikTok)" : "Tỷ lệ 9:16 Dọc Shorts/TikTok (Nhấn để đổi sang 16:9 Ngang)"}
                >
                  <span>{cropRatio === '16:9' ? '📺 16:9 YouTube' : '📱 9:16 Shorts'}</span>
                </button>

                {/* Cột 4 (BÊN PHẢI CÙNG): Nút Toggle Xem Phim ⇄ Làm Video (Kích Thước Tĩnh 100%, Căn Giữa) */}
                <button
                  type="button"
                  onClick={() => setVideoViewMode(prev => prev === 'final' ? 'raw' : 'final')}
                  style={{
                    width: '100%',
                    height: '34px',
                    fontSize: '12.5px',
                    fontWeight: 800,
                    borderRadius: '6px',
                    cursor: 'pointer',
                    border: videoViewMode === 'final' ? '1.5px solid #10b981' : '1.5px solid #64748b',
                    background: videoViewMode === 'final' ? '#10b981' : '#f1f5f9',
                    color: videoViewMode === 'final' ? '#ffffff' : '#334155',
                    boxShadow: videoViewMode === 'final' ? '0 1px 4px rgba(16,185,129,0.3)' : 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '6px',
                    transition: 'all 0.15s ease',
                    boxSizing: 'border-box'
                  }}
                  title={videoViewMode === 'final' ? "Đang ở chế độ Xem Thuyết Minh hoàn thiện (Nhấn để chuyển sang Căn Chỉnh Làm Video)" : "Đang ở chế độ Căn Chỉnh Làm Video (Nhấn để chuyển sang Xem Thuyết Minh)"}
                >
                  <span>{videoViewMode === 'final' ? '🎬 Xem Thuyết Minh' : '🛠️ Làm Video (Căn Chỉnh)'}</span>
                </button>
              </div>
            </div>

            {/* VIDEO CANVAS (CHUẨN TỈ LỆ VIDEO - FULL WIDTH KHÔNG VIỀN ĐEN, KHÔNG LỆCH TỌA ĐỘ) */}
            <div
              ref={containerRef}
              onMouseDown={handleCanvasMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              style={{
                position: 'relative',
                width: '100%',
                aspectRatio: videoRatio || '16 / 9',
                background: '#090d16',
                borderRadius: '8px',
                border: '1.5px solid #334155',
                overflow: 'hidden',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                userSelect: 'none',
                cursor: videoViewMode === 'raw' && interactionMode === 'crop-bottom' ? 'ns-resize' : 'default',
                boxShadow: '0 2px 10px rgba(0, 0, 0, 0.25)'
              }}
            >
              {selectedProject ? (
                <video
                  ref={videoRef}
                  key={`${selectedProject.id}_${videoViewMode}_${videoTimestamp}`}
                  preload="metadata"
                  playsInline
                  onTimeUpdate={handleTimeUpdate}
                  onLoadedMetadata={handleLoadedMetadata}
                  onEnded={() => setIsPlaying(false)}
                  onClick={togglePlayPause}
                  style={{ width: '100%', height: '100%', objectFit: 'fill', display: 'block', cursor: 'pointer' }}
                  src={`/api/v1/projects/${selectedProject.id}/video?type=${videoViewMode}&_t=${videoTimestamp}`}
                />
              ) : (
                <div style={{ color: 'var(--text-dim)', textAlign: 'center', pointerEvents: 'none' }}>
                  <Film size={38} style={{ opacity: 0.3, marginBottom: '6px' }} />
                  <div style={{ fontSize: '12px' }}>Chưa chọn video nào. Hãy chọn ở danh sách bên phải!</div>
                </div>
              )}

              {/* CẮT SUB & TỰ ĐỘNG CÂN TỶ LỆ CHUẨN YOUTUBE PREVIEW OVERLAYS (Chỉ hiện khi căn chỉnh video gốc) */}
              {(videoViewMode === 'raw') && (
                <>
                  {/* 1. LỚP PHỦ CẮT DỌC MÉP TRÁI */}
                  {targetLeft > 0.4 && (
                    <div
                      style={{
                        position: 'absolute',
                        top: 0,
                        bottom: 0,
                        left: 0,
                        width: `${targetLeft}%`,
                        background: 'rgba(5, 8, 15, 0.70)',
                        borderRight: '1.5px dashed rgba(56, 189, 248, 0.6)',
                        pointerEvents: 'none',
                        zIndex: 18,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center'
                      }}
                    >
                      <span style={{ fontSize: '10px', fontWeight: 800, color: 'rgba(255, 255, 255, 0.6)', writingMode: 'vertical-rl' }}>
                        ✂ Cắt {targetLeft.toFixed(1)}%
                      </span>
                    </div>
                  )}

                  {/* 2. LỚP PHỦ CẮT DỌC MÉP PHẢI */}
                  {targetLeft > 0.4 && (
                    <div
                      style={{
                        position: 'absolute',
                        top: 0,
                        bottom: 0,
                        left: `${targetRight}%`,
                        right: 0,
                        background: 'rgba(5, 8, 15, 0.70)',
                        borderLeft: '1.5px dashed rgba(56, 189, 248, 0.6)',
                        pointerEvents: 'none',
                        zIndex: 18,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center'
                      }}
                    >
                      <span style={{ fontSize: '10px', fontWeight: 800, color: 'rgba(255, 255, 255, 0.6)', writingMode: 'vertical-rl' }}>
                        ✂ Cắt {targetLeft.toFixed(1)}%
                      </span>
                    </div>
                  )}

                  {/* 2.5 VÙNG CẮT MÉP TRÊN (ĐỎ CẢNH BÁO) */}
                  {topCutPercent > 0 && (
                    <>
                      <div
                        style={{
                          position: 'absolute',
                          top: 0,
                          height: `${topCutPercent}%`,
                          left: 0,
                          right: 0,
                          background: 'rgba(239, 68, 68, 0.32)',
                          borderBottom: '2px dashed #ef4444',
                          pointerEvents: 'none',
                          zIndex: 20,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center'
                        }}
                      >
                        <span style={{
                          fontSize: '11px',
                          fontWeight: 800,
                          color: '#ffffff',
                          background: 'rgba(185, 28, 28, 0.88)',
                          padding: '2px 8px',
                          borderRadius: '4px',
                          boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
                          letterSpacing: '0.3px'
                        }}>
                          ✂ VÙNG CẮT MÉP TRÊN (-{topCutPercent}%)
                        </span>
                      </div>

                      {/* THANH KÉO ĐỘ CAO CẮT MÉP TRÊN */}
                      <div
                        onMouseDown={(e) => handleMouseDown(e, 'crop-top')}
                        style={{
                          position: 'absolute',
                          top: `calc(${topCutPercent}% - 8px)`,
                          left: 0,
                          right: 0,
                          height: '16px',
                          cursor: 'ns-resize',
                          zIndex: 35
                        }}
                        title="Kéo mép này lên/xuống để chọn độ cao cắt mép trên"
                      />
                    </>
                  )}

                  {/* 3. VÙNG CẮT SUB Ở MÉP ĐÁY (ĐỎ CẢNH BÁO) */}
                  {maskHeight > 0 && (
                    <>
                      <div
                        style={{
                          position: 'absolute',
                          top: `calc(100% - ${maskHeight}%)`,
                          bottom: 0,
                          left: 0,
                          right: 0,
                          background: 'rgba(239, 68, 68, 0.32)',
                          borderTop: '2px dashed #ef4444',
                          pointerEvents: 'none',
                          zIndex: 20,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center'
                        }}
                      >
                        <span style={{
                          fontSize: '11px',
                          fontWeight: 800,
                          color: '#ffffff',
                          background: 'rgba(185, 28, 28, 0.88)',
                          padding: '2px 8px',
                          borderRadius: '4px',
                          boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
                          letterSpacing: '0.3px'
                        }}>
                          ✂ VÙNG CẮT BỎ SUB TRUNG GỐC (-{maskHeight}%)
                        </span>
                      </div>

                      {/* 4. THANH KÉO ĐỘ CAO CẮT SUB ĐÁY */}
                      <div
                        onMouseDown={(e) => handleMouseDown(e, 'crop-bottom')}
                        style={{
                          position: 'absolute',
                          top: `calc(100% - ${maskHeight}% - 8px)`,
                          left: 0,
                          right: 0,
                          height: '16px',
                          cursor: 'ns-resize',
                          zIndex: 35
                        }}
                        title="Kéo mép này lên/xuống để chọn độ cao cắt sub đáy"
                      />
                    </>
                  )}

                  {/* 5. KHUNG THÀNH PHẨM YOUTUBE / SHORTS */}
                  <div
                    style={{
                      position: 'absolute',
                      top: `${topCutPercent}%`,
                      height: `${remainH}%`,
                      left: `${targetLeft}%`,
                      width: `${targetW}%`,
                      border: '2px solid rgba(56, 189, 248, 0.95)',
                      boxShadow: '0 0 20px rgba(56, 189, 248, 0.3)',
                      pointerEvents: 'none',
                      zIndex: 19
                    }}
                  >
                    <div style={{
                      position: 'absolute',
                      top: '6px',
                      left: '50%',
                      transform: 'translateX(-50%)',
                      background: 'rgba(2, 132, 199, 0.88)',
                      color: '#ffffff',
                      fontSize: '10px',
                      fontWeight: 800,
                      padding: '2px 8px',
                      borderRadius: '4px',
                      whiteSpace: 'nowrap',
                      boxShadow: '0 2px 8px rgba(0,0,0,0.5)'
                    }}>
                      {cropRatio === '16:9'
                        ? '🎬 Chuẩn 16:9 YouTube (Giữ Trọn Hình - 0% Scale)'
                        : '📱 Chuẩn 9:16 (Shorts/TikTok) - 0% Scale'}
                    </div>
                  </div>
                </>
              )}

              {/* DYNAMIC SUBTITLE PREVIEW (Chỉ hiện khi ở tab Video Gốc để căn chỉnh phụ đề) */}
              {(!noSub && videoViewMode === 'raw') && (
                hasMask ? (
                  /* 1. Nền mờ ôm vừa đủ độ rộng sub — CSS max-content, canh giữa */
                  <div
                    onMouseDown={(e) => handleMouseDown(e, 'sub-move')}
                    style={{
                      position: 'absolute',
                      bottom: `calc(${maskHeight}% + ${subBottomOffset}%)`,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      width: 'max-content',
                      maxWidth: '96%',
                      height: `${blurHeight}%`,
                      padding: '0 28px',
                      background: 'rgba(10, 14, 22, 0.68)',
                      backdropFilter: 'blur(10px) saturate(1.2)',
                      borderRadius: '6px',
                      borderTop: '1px solid rgba(255,255,255,0.10)',
                      borderBottom: '1px solid rgba(255,255,255,0.10)',
                      boxShadow: '0 2px 24px rgba(0,0,0,0.55)',
                      cursor: 'ns-resize',
                      zIndex: 25,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      boxSizing: 'border-box',
                      overflow: 'hidden'
                    }}
                    title="Bấm phím ↑ / ↓ hoặc kéo chuột để di chuyển cao độ dải mờ & phụ đề"
                  >
                    {/* Chữ Phụ Đề — luôn ở chính giữa nền */}
                    <span
                      style={{
                        position: 'relative',
                        zIndex: 26,
                        whiteSpace: 'nowrap',
                        fontSize: `${Math.max(13, Math.round(subFontSize * 1.05))}px`,
                        fontWeight: 900,
                        color: '#ffffff',
                        textShadow: '0 0 4px #000, 1px 1px 3px #000, -1px -1px 3px #000',
                        letterSpacing: '0.4px',
                        lineHeight: 1.3,
                        pointerEvents: 'none',
                        flexShrink: 0
                      }}
                    >
                      Lúc này hắn mới nhận ra điều bất thường... ({subFontSize}px)
                    </span>
                  </div>
                ) : (
                  /* 2. Chữ Phụ Đề Khi Không Có Dải Mờ Che */
                  <div
                    onMouseDown={(e) => handleMouseDown(e, 'sub-move')}
                    style={{
                      position: 'absolute',
                      bottom: `${subBottomOffset}%`,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      textAlign: 'center',
                      cursor: 'ns-resize',
                      zIndex: 25,
                      userSelect: 'none',
                      maxWidth: '92%'
                    }}
                    title="Bấm phím ↑ / ↓ hoặc kéo chuột để di chuyển cao độ phụ đề"
                  >
                    <span style={{
                      display: 'inline-block',
                      fontSize: `${Math.max(13, Math.round(subFontSize * 1.05))}px`,
                      fontWeight: 900,
                      color: '#FFE600',
                      textShadow: '0 0 3px #000, 2px 2px 3px #000, -2px -2px 3px #000, 2px -2px 3px #000, -2px 2px 3px #000',
                      letterSpacing: '0.4px',
                      whiteSpace: 'nowrap'
                    }}>
                      Lúc này hắn mới nhận ra điều bất thường... ({subFontSize}px)
                    </span>
                  </div>
                )
              )}
            </div>

            {/* COMPACT PLAYBACK CONTROL BAR */}
            <div style={{
              background: '#f8fafc',
              border: '1px solid #cbd5e1',
              borderRadius: '6px',
              padding: '5px 10px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}>
              {/* Nút Tua Lùi 10s */}
              <button
                type="button"
                onClick={() => handleSkip(-10)}
                title="Tua lùi 10 giây (Phím ← hoặc J)"
                style={{
                  width: '26px',
                  height: '26px',
                  borderRadius: '50%',
                  background: '#ffffff',
                  border: '1px solid #cbd5e1',
                  color: '#334155',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  flexShrink: 0,
                  transition: 'all 0.15s'
                }}
              >
                <RotateCcw size={12} />
              </button>

              {/* Nút Play / Pause */}
              <button
                type="button"
                onClick={togglePlayPause}
                title="Phát / Tạm dừng (Phím Space hoặc K)"
                style={{
                  width: '28px',
                  height: '28px',
                  borderRadius: '50%',
                  background: '#2563eb',
                  border: 'none',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  flexShrink: 0
                }}
              >
                {isPlaying ? <Pause size={13} /> : <Play size={13} style={{ marginLeft: '1px' }} />}
              </button>

              {/* Nút Tua Tới 10s */}
              <button
                type="button"
                onClick={() => handleSkip(10)}
                title="Tua tới 10 giây (Phím → hoặc L)"
                style={{
                  width: '26px',
                  height: '26px',
                  borderRadius: '50%',
                  background: '#ffffff',
                  border: '1px solid #cbd5e1',
                  color: '#334155',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  flexShrink: 0,
                  transition: 'all 0.15s'
                }}
              >
                <RotateCw size={12} />
              </button>

              <div style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', fontWeight: 700, color: '#334155', whiteSpace: 'nowrap' }}>
                {formatDuration(currentTime)} / {formatDuration(duration)}
              </div>

              <input
                type="range"
                min={0}
                max={duration || 100}
                value={currentTime}
                onChange={handleSeek}
                style={{ flex: 1, accentColor: '#2563eb', cursor: 'pointer', height: '4px' }}
              />

              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <button onClick={toggleMute} style={{ background: 'transparent', border: 'none', color: '#475569', cursor: 'pointer', display: 'flex', alignItems: 'center' }}>
                  {isMuted || volume === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />}
                </button>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={isMuted ? 0 : volume}
                  onChange={handleVolumeChange}
                  style={{ width: '50px', accentColor: '#2563eb', cursor: 'pointer', height: '4px' }}
                />
              </div>

              <button onClick={handleFullscreen} title="Toàn màn hình" style={{ background: 'transparent', border: 'none', color: '#475569', cursor: 'pointer', display: 'flex', alignItems: 'center' }}>
                <Maximize2 size={14} />
              </button>
            </div>
          </div>

          {/* 2. TIẾN TRÌNH XỬ LÝ & TRẠNG THÁI (PROGRESS CARD GỌN GÀNG THEO CHUẨN AIREAD) */}
          <div style={{
            background: '#ffffff',
            border: '1.5px solid #cbd5e1',
            borderRadius: '10px',
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
            boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)'
          }}>
            {/* Header: Title + Status Badge */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 800, color: '#0f172a' }}>
                <Sparkles size={15} color="#2563eb" /> Tiến Trình Tự Động Hóa (AI Pipeline)
              </div>
              <span style={{
                fontSize: '11px',
                fontWeight: 800,
                padding: '2px 8px',
                borderRadius: '6px',
                background: isRunning ? '#eff6ff' : selectedProject?.has_final ? '#ecfdf5' : '#f1f5f9',
                color: isRunning ? '#2563eb' : selectedProject?.has_final ? '#059669' : '#64748b',
                border: `1px solid ${isRunning ? '#bfdbfe' : selectedProject?.has_final ? '#a7f3d0' : '#e2e8f0'}`
              }}>
                {isRunning ? `Đang xử lý: ${progress}% (Bước ${currentStep}/5)` : selectedProject?.has_final ? 'Hoàn thành 100%' : 'Sẵn sàng'}
              </span>
            </div>

            {/* Glowing Animated Progress Bar */}
            <div style={{ width: '100%', height: '8px', background: '#e2e8f0', borderRadius: '999px', overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${isRunning ? Math.max(5, progress) : (selectedProject?.has_final ? 100 : 0)}%`,
                background: 'linear-gradient(90deg, #2563eb 0%, #06b6d4 50%, #10b981 100%)',
                borderRadius: '999px',
                transition: 'width 0.4s ease'
              }} />
            </div>

            {/* 5-Step Visual Flow Badges */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '6px' }}>
              {[
                { step: 1, label: '1. Tiền Xử Lý' },
                { step: 2, label: '2. Bóc Thực Thể' },
                { step: 3, label: '3. Dịch AI' },
                { step: 4, label: '4. TTS TikTok' },
                { step: 5, label: '5. Render Video' }
              ].map((s) => {
                const isStepActive = isRunning && currentStep === s.step;
                const isStepDone = (!isRunning && selectedProject?.has_final) || (isRunning && currentStep > s.step);
                return (
                  <div
                    key={s.step}
                    style={{
                      padding: '4px 2px',
                      borderRadius: '5px',
                      textAlign: 'center',
                      fontSize: '10.5px',
                      fontWeight: 700,
                      background: isStepDone ? '#ecfdf5' : isStepActive ? '#eff6ff' : '#f8fafc',
                      color: isStepDone ? '#059669' : isStepActive ? '#2563eb' : '#94a3b8',
                      border: `1px solid ${isStepDone ? '#a7f3d0' : isStepActive ? '#93c5fd' : '#e2e8f0'}`,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis'
                    }}
                  >
                    {isStepDone ? `✔ ${s.label}` : isStepActive ? `⚡ ${s.label}` : s.label}
                  </div>
                );
              })}
            </div>

            {/* Realtime Action Status Box */}
            <div style={{
              background: '#f8fafc',
              border: '1px solid #e2e8f0',
              borderRadius: '6px',
              padding: '6px 10px',
              fontSize: '11.5px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              color: '#334155'
            }}>
              <span style={{
                display: 'inline-block',
                width: '7px',
                height: '7px',
                borderRadius: '50%',
                background: isRunning ? '#10b981' : '#94a3b8',
                flexShrink: 0
              }} />
              <span style={{ fontWeight: 600, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {logs.length > 0 ? logs[logs.length - 1].text : 'Sẵn sàng xử lý kịch bản. Bấm nút bên phải để chạy.'}
              </span>
            </div>

            {(result || selectedProject?.has_final) && (
              <a
                href={`/api/v1/projects/download-video/${selectedProject?.id || result?.project_id}`}
                download
                className="btn btn-emerald"
                style={{ padding: '8px', textAlign: 'center', textDecoration: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', fontSize: '12px', flexShrink: 0 }}
              >
                <Download size={13} /> Tải Video Hoàn Chỉnh (.mp4)
              </a>
            )}
          </div>
        </div>

        {/* RIGHT COLUMN: CONFIG CARD + SCROLLABLE VIDEO LIST (FILLS FULL HEIGHT) */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', height: '100%', minHeight: 0, minWidth: 0, overflow: 'hidden' }}>
          
          {/* 1. CONFIG CARD (HỢP NHẤT LINK + GIỌNG + THỂ LOẠI + 1-CLICK) */}
          <div style={{
            background: '#ffffff',
            border: '1.5px solid #cbd5e1',
            borderRadius: '10px',
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
            boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)',
            flexShrink: 0
          }}>
            {/* 1. KHUNG GÁN LINK VIDEO & CHỌN ĐỘ PHÂN GIẢI (GỌN GÀNG, RỘNG RÃI, DỄ DÙNG) */}
            <div style={{
              background: '#f0f9ff',
              border: '1.5px solid #38bdf8',
              borderRadius: '8px',
              padding: '10px 12px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              boxShadow: '0 2px 8px rgba(56, 189, 248, 0.15)'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <label style={{ fontSize: '12.5px', color: '#0369a1', fontWeight: 800, margin: 0, display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <LinkIcon size={14} /> Dán Link Video Mới (Bilibili / YouTube / Douyin):
                </label>
                <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                  {selectedProject?.source_url && (
                    <a
                      href={selectedProject.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`Mở link gốc của Video #${selectedProject.id}: ${selectedProject.source_url}`}
                      style={{
                        padding: '2px 8px',
                        background: '#eff6ff',
                        border: '1px solid #bfdbfe',
                        borderRadius: '4px',
                        fontSize: '11px',
                        fontWeight: 700,
                        color: '#1d4ed8',
                        textDecoration: 'none',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px'
                      }}
                    >
                      <ExternalLink size={11} /> Link gốc #{selectedProject.id}
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={handlePasteFromClipboard}
                    title="Dán nhanh từ Clipboard"
                    style={{
                      padding: '2px 8px',
                      background: '#e0f2fe',
                      border: '1px solid #7dd3fc',
                      borderRadius: '4px',
                      fontSize: '11px',
                      fontWeight: 700,
                      color: '#0369a1',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px'
                    }}
                  >
                    <Clipboard size={11} /> Dán Link
                  </button>
                </div>
              </div>

              {/* Ô nhập link full width rộng rãi */}
              <input
                type="text"
                placeholder="Dán link web hoặc mã BV vào đây (ví dụ: BV1k6cPzvEE7 hoặc https://...)..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={isRunning}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  fontSize: '12.5px',
                  background: '#ffffff',
                  border: '1.5px solid #0284c7',
                  borderRadius: '6px',
                  fontWeight: 600,
                  color: '#0f172a',
                  boxSizing: 'border-box'
                }}
              />

              {/* Dòng chọn độ phân giải và nút Tải Về */}
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '11.5px', fontWeight: 700, color: '#0369a1', whiteSpace: 'nowrap' }}>Độ phân giải:</span>
                  <select
                    value={downloadQuality}
                    onChange={(e) => setDownloadQuality(e.target.value)}
                    disabled={isRunning}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      fontSize: '12px',
                      fontWeight: 700,
                      background: '#ffffff',
                      border: '1px solid #0284c7',
                      borderRadius: '6px',
                      color: '#0369a1',
                      cursor: 'pointer'
                    }}
                    title="Sắp xếp tuần tự từ cao xuống thấp"
                  >
                    <option value="1080p">🌟 1080p Full HD (60fps/30fps)</option>
                    <option value="720p">⚡ 720p HD (60fps/30fps)</option>
                    <option value="480p">🌾 480p SD (Tiêu chuẩn nhẹ)</option>
                    <option value="360p">💾 360p (Tiết kiệm dung lượng)</option>
                    <option value="audio">🎵 Chỉ Tải Audio (16kHz)</option>
                  </select>
                </div>

                <button
                  className="btn btn-primary btn-sm"
                  onClick={handleDownloadOnly}
                  disabled={isDownloading || isRunning || !url.trim()}
                  style={{
                    whiteSpace: 'nowrap',
                    padding: '6px 14px',
                    fontSize: '12px',
                    fontWeight: 800,
                    background: '#0284c7',
                    color: '#ffffff',
                    borderRadius: '6px',
                    boxShadow: '0 2px 6px rgba(2, 132, 199, 0.25)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}
                >
                  {isDownloading ? <RefreshCw size={12} className="animate-spin" /> : <Download size={13} />} Tải Về
                </button>
              </div>
            </div>

            {/* Voice & Genre Row */}
            <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '10px' }}>
              <div>
                <label style={{ fontSize: '12px', color: '#0f172a', fontWeight: 800, display: 'block', marginBottom: '4px' }}>
                  Giọng Đọc Lồng Tiếng:
                </label>
                <div style={{ display: 'flex', gap: '5px' }}>
                  <select
                    value={voiceCode}
                    onChange={(e) => setVoiceCode(e.target.value)}
                    style={{ flex: 1, padding: '6px 8px', fontSize: '12px', background: '#f8fafc', border: '1px solid #cbd5e1' }}
                  >
                    {ALL_VOICES.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={handleTestVoiceSample}
                    disabled={isPlayingSample}
                    title="Bấm để nghe thử giọng mẫu"
                    style={{ padding: '0 10px', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11.5px', whiteSpace: 'nowrap' }}
                  >
                    {isPlayingSample ? <RefreshCw size={11} className="animate-spin" /> : <Headphones size={13} color="#2563eb" />}
                    <span>Test</span>
                  </button>
                </div>
              </div>

              <div>
                <label style={{ fontSize: '12px', color: '#0f172a', fontWeight: 800, display: 'block', marginBottom: '4px' }}>
                  Thể Loại AIREAD:
                </label>
                <select
                  value={genre}
                  onChange={(e) => setGenre(e.target.value)}
                  style={{ width: '100%', padding: '6px 8px', fontSize: '12px', background: '#f8fafc', border: '1px solid #cbd5e1' }}
                >
                  {AIREAD_GENRES.map((g) => (
                    <option key={g.code} value={g.code}>
                      {g.icon} {g.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* 1-Click Action Button & Stop Button */}
            {isRunning ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: '8px', marginTop: '2px' }}>
                <button
                  className="btn btn-primary"
                  disabled
                  style={{
                    padding: '10px 8px',
                    fontSize: '12px',
                    fontWeight: 800,
                    background: 'linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%)',
                    cursor: 'wait'
                  }}
                >
                  <RefreshCw size={14} className="animate-spin" /> Đang Chạy ({progress}%)...
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={handleCancelAutomation}
                  title="Dừng tiến trình: Bảo toàn nguyên vẹn các lô đã dịch xong, chỉ dọn dẹp các chunk audio/video tạm"
                  style={{
                    padding: '10px 8px',
                    fontSize: '11.5px',
                    fontWeight: 900,
                    background: '#dc2626',
                    color: '#ffffff',
                    border: '1px solid #b91c1c',
                    boxShadow: '0 2px 8px rgba(220, 38, 38, 0.35)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '4px'
                  }}
                >
                  🛑 DỪNG TIẾN TRÌNH
                </button>
              </div>
            ) : (
              <button
                className="btn btn-primary"
                onClick={handleStartAutomation}
                style={{
                  width: '100%',
                  padding: '10px',
                  fontSize: '12.5px',
                  fontWeight: 900,
                  marginTop: '2px',
                  background: noSub
                    ? 'linear-gradient(135deg, #d97706 0%, #b45309 100%)'
                    : 'linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%)',
                  boxShadow: noSub
                    ? '0 4px 12px rgba(217, 119, 6, 0.25)'
                    : '0 4px 12px rgba(37, 99, 235, 0.25)'
                }}
              >
                <Zap size={14} /> {noSub ? 'BẮT ĐẦU (DỊCH ➔ TTS ➔ CROP KHÔNG SUB)' : 'BẮT ĐẦU TỰ ĐỘNG HÓA 100% (DỊCH ➔ TTS ➔ RENDER)'}
              </button>
            )}
          </div>

          {/* 2. COMPACT VERTICAL VIDEO LIST (KÉO DÀI TRỌN VẸN TỚI ĐÁY KHUNG PHẢI) */}
          <div style={{
            background: '#ffffff',
            border: '1.5px solid #cbd5e1',
            borderRadius: '10px',
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            flex: 1,
            minHeight: 0,
            boxShadow: '0 4px 12px rgba(15, 23, 42, 0.05)'
          }}>
            {/* Header + Search Bar */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
              <div style={{ fontSize: '13px', fontWeight: 800, color: '#0f172a', whiteSpace: 'nowrap' }}>
                📋 Danh Sách Video ({projects.length})
              </div>
              
              <div style={{ position: 'relative', width: '140px' }}>
                <Search size={11} style={{ position: 'absolute', left: '7px', top: '50%', transform: 'translateY(-50%)', color: '#94a3b8' }} />
                <input
                  type="text"
                  placeholder="Lọc video..."
                  value={videoSearch}
                  onChange={(e) => setVideoSearch(e.target.value)}
                  style={{ padding: '4px 6px 4px 22px', fontSize: '11.5px', width: '100%', background: '#f8fafc', border: '1px solid #cbd5e1', borderRadius: '4px' }}
                />
              </div>
            </div>

            {/* Scrollable List Area filling all remaining space */}
            <div style={{
              flex: 1,
              minHeight: 0,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
              paddingRight: '2px'
            }}>
              {filteredProjects.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '30px', color: 'var(--text-dim)', fontSize: '12px' }}>
                  Không tìm thấy video nào.
                </div>
              ) : (
                filteredProjects.map((p) => {
                  const isSelected = selectedProject?.id === p.id;
                  const activeT = activeTasksMap[p.id];
                  return (
                    <div
                      key={p.id}
                      onClick={() => {
                        setSelectedProject(p);
                        loadProjectServerLogs(p.id);
                        if (activeTasksMap[p.id]) {
                          const curT = activeTasksMap[p.id];
                          setTaskId(curT.id);
                          setIsRunning(true);
                          setProgress(curT.progress || 0);
                          setCurrentStep(curT.step || 1);
                        } else {
                          setIsRunning(false);
                          setTaskId(null);
                        }
                      }}
                      style={{
                        padding: '8px 10px',
                        background: isSelected ? '#eff6ff' : '#f8fafc',
                        border: isSelected ? '1.5px solid #2563eb' : (activeT ? '1.5px solid #10b981' : '1px solid #e2e8f0'),
                        borderRadius: '8px',
                        cursor: 'pointer',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                        transition: 'all 0.15s ease'
                      }}
                      onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = '#edf2f7'; }}
                      onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = '#f8fafc'; }}
                    >
                      {/* Hàng 1: Tiêu đề video + Cụm Action Icon vuông mini */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px' }}>
                        <div style={{
                          flex: 1,
                          minWidth: 0,
                          fontSize: '12px',
                          fontWeight: isSelected ? 800 : 700,
                          color: isSelected ? '#1d4ed8' : '#0f172a',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis'
                        }} title={`#${p.id} - ${p.title}`}>
                          #{p.id} - {p.title}
                        </div>

                        {/* Cụm Action Icon vuông mini (22x22px) */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '3px', flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                          {/* Nút Link gốc */}
                          {p.source_url && (
                            <a
                              href={p.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              style={{
                                width: '22px',
                                height: '22px',
                                display: 'inline-flex',
                                alignItems: 'center',
                                justifySelf: 'center',
                                justifyContent: 'center',
                                borderRadius: '4px',
                                background: '#e0f2fe',
                                color: '#0284c7',
                                border: '1px solid #bae6fd',
                                textDecoration: 'none',
                                transition: 'all 0.15s ease'
                              }}
                              onMouseEnter={(e) => { e.currentTarget.style.background = '#bae6fd'; e.currentTarget.style.color = '#0369a1'; }}
                              onMouseLeave={(e) => { e.currentTarget.style.background = '#e0f2fe'; e.currentTarget.style.color = '#0284c7'; }}
                              title={`🔗 Mở link gốc trong tab mới: ${p.source_url}`}
                            >
                              <ExternalLink size={11} />
                            </a>
                          )}

                          {/* Nút Reset Dịch */}
                          <button
                            type="button"
                            onClick={(e) => handleResetTranslation(e, p.id, p.title)}
                            style={{
                              width: '22px',
                              height: '22px',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              borderRadius: '4px',
                              background: '#fffbeb',
                              border: '1px solid #fcd34d',
                              color: '#d97706',
                              cursor: 'pointer',
                              padding: 0,
                              transition: 'all 0.15s ease'
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = '#fef3c7'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = '#fffbeb'; }}
                            title="Reset Bản Dịch (giữ video gốc & câu thoại Trung ASR, xóa bản dịch/audio/video render để làm lại)"
                          >
                            <RotateCcw size={11} />
                          </button>

                          {/* Nút Reset Mới Tải */}
                          <button
                            type="button"
                            onClick={(e) => handleResetDownloaded(e, p.id, p.title)}
                            style={{
                              width: '22px',
                              height: '22px',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              borderRadius: '4px',
                              background: '#fef2f2',
                              border: '1px solid #fca5a5',
                              color: '#b91c1c',
                              cursor: 'pointer',
                              padding: 0,
                              transition: 'all 0.15s ease'
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = '#fee2e2'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = '#fef2f2'; }}
                            title="Reset Về Mới Tải (xóa sạch toàn bộ câu thoại/bản dịch/audio/video render, chỉ giữ video gốc)"
                          >
                            <RefreshCw size={11} />
                          </button>

                          {/* Nút Xóa */}
                          <button
                            type="button"
                            onClick={(e) => handleDeleteProject(e, p.id, p.title)}
                            style={{
                              width: '22px',
                              height: '22px',
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              borderRadius: '4px',
                              background: 'transparent',
                              border: 'none',
                              color: '#94a3b8',
                              cursor: 'pointer',
                              padding: 0,
                              transition: 'all 0.15s ease'
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.background = '#fee2e2'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.background = 'transparent'; }}
                            title="Xóa vĩnh viễn video này và toàn bộ dữ liệu/file liên quan"
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </div>

                      {/* Hàng 2: Metadata + Badge trạng thái mini + Trường Link gốc */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px', fontSize: '11px', color: '#64748b' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'nowrap', overflow: 'hidden' }}>
                          {activeT ? (
                            <span style={{
                              background: '#dcfce7',
                              color: '#15803d',
                              border: '1px solid #86efac',
                              padding: '1px 6px',
                              borderRadius: '10px',
                              fontSize: '10px',
                              fontWeight: 800,
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '3px',
                              animation: 'pulse 1.5s infinite'
                            }} title={`Đang chạy: ${activeT.last_log || 'Đang xử lý...'}`}>
                              ⚡ {activeT.progress || 0}%
                            </span>
                          ) : getStatusBadge(p)}
                          <span>⏱ {formatDuration(p.duration)}</span>
                          <span>💬 {p.total_dialogues} câu</span>
                        </div>

                        {/* Trường hiển thị Link gốc rút gọn */}
                        {p.source_url ? (
                          <a
                            href={p.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            style={{
                              fontSize: '10.5px',
                              color: '#0284c7',
                              textDecoration: 'none',
                              maxWidth: '130px',
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '2px',
                              flexShrink: 0
                            }}
                            title={`Link gốc: ${p.source_url} (Bấm để mở trang)`}
                            onMouseEnter={(e) => { e.currentTarget.style.textDecoration = 'underline'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.textDecoration = 'none'; }}
                          >
                            🔗 {p.source_url.replace(/^https?:\/\/(www\.)?/, '').split('?')[0]}
                          </a>
                        ) : (
                          <span style={{ fontSize: '10px', color: '#94a3b8', fontStyle: 'italic' }}>
                            (File cục bộ)
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
