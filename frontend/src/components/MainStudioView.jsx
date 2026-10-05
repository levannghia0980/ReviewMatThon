import React, { useState, useEffect, useRef, useMemo } from 'react';
import { 
  Zap, Play, Pause, Download, Sparkles, Volume2, VolumeX, Film, CheckCircle2, 
  RefreshCw, RotateCcw, Terminal, Eye, Sliders, Music, Clock, FileText, ArrowRight, Plus, Minus, Trash2,
  Maximize2, ArrowUp, ArrowDown, MoveVertical, Search, Headphones, Clipboard, Link as LinkIcon
} from 'lucide-react';
import { AIREAD_GENRES, DEFAULT_GENRE } from '../constants/genres';
import { ALL_VOICES } from '../constants/voices';

export default function MainStudioView({ onNavigateTab }) {
  const [url, setUrl] = useState('');
  const [isDownloading, setIsDownloading] = useState(false);
  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState(null);
  const [videoSearch, setVideoSearch] = useState('');

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
  const [maskTop, setMaskTop] = useState(80); // % from top
  const [maskLeft, setMaskLeft] = useState(0); // % left
  const [maskWidth, setMaskWidth] = useState(100); // Giữ để gửi backend
  const [maskHeight, setMaskHeight] = useState(8); // % height cắt sub đáy
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

  const handleResetProject = async (e, pId, pTitle) => {
    if (e) e.stopPropagation();
    const targetId = pId || selectedProject?.id;
    const targetTitle = pTitle || selectedProject?.title || targetId;
    if (!targetId) return;

    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET VIDEO NÀY VỀ LÚC MỚI TẢI XONG?\n\n"${targetTitle}" (ID: #${targetId})\n\nThao tác này sẽ:\n✓ GIỮ NGUYÊN 100% FILE VIDEO GỐC ĐÃ TẢI VỀ\n✗ Xóa sạch toàn bộ câu thoại đã bóc tách & bản dịch\n✗ Xóa toàn bộ audio thuyết minh TTS & video render hoàn chỉnh\n\nVideo sẽ trở về trạng thái như lúc mới tải xong để bạn sẵn sàng chạy lại!`)) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/projects/${targetId}/reset`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        await loadProjects();
        if (selectedProject?.id === targetId) {
          setSelectedProject(prev => prev ? {
            ...prev,
            status: 'DOWNLOADED',
            total_dialogues: 0,
            translated_dialogues: 0,
            has_final: false,
            final_video_path: null,
            srt_path: null,
            txt_path: null
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
  }, []);

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

  // Poll Task Status
  useEffect(() => {
    let interval = null;
    if (isRunning && taskId) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/pipeline/status/${taskId}`);
          if (!res.ok) return;
          const data = await res.json();
          setProgress(data.progress || 0);
          setCurrentStep(data.step || 1);
          if (data.logs) setLogs(data.logs);

          if (data.status === 'completed') {
            setIsRunning(false);
            setResult(data.result);
            if (data.result?.final_video_path) {
              setVideoViewMode('final');
            } else {
              setVideoViewMode('raw');
            }
            clearInterval(interval);
            loadProjects(data.result?.project_id);
            window.dispatchEvent(new CustomEvent('video-projects-updated'));
          } else if (data.status === 'failed') {
            setIsRunning(false);
            clearInterval(interval);
            alert(`Lỗi: ${data.error}`);
          }
        } catch (e) {
          console.error(e);
        }
      }, 1200);
    }
    return () => clearInterval(interval);
  }, [isRunning, taskId]);

  // Keyboard navigation for Subtitle Up/Down & Play/Pause
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSubBottomOffset(prev => Math.min(85, prev + 1));
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSubBottomOffset(prev => Math.max(1, prev - 1));
      } else if (e.key === ' ') {
        e.preventDefault();
        togglePlayPause();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isPlaying]);

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
        provider: 'gemini',
        voice_code: voiceCode,
        margin_v: finalMarginV, // % khoảng cách từ đáy đến chữ phụ đề
        backdrop_opacity_hex: backdropOpacity,
        has_mask: hasMask,
        mask_top: calcMaskTop, // % từ đỉnh xuống dải mờ
        mask_left: calcMaskLeft,
        mask_width: calcMaskWidth, // Độ rộng dải mờ %
        mask_height: calcBlurH, // Chiều cao dải mờ (Cao Nền) %
        crop_ratio: cropRatio,
        font_size: subFontSize,
        box_style: 'white_box',
        box_padding: subBoxPadding,
        karaoke_highlight_color: '&H000000FF',
        channel_name: '@Mắt Thần Review',
        channel_opacity: 0.35,
        logo_position: 'top_left',
        logo_size: 120,
        batch_size: batchSize
      };

      if (selectedProject) {
        payload.project_id = selectedProject.id;
      } else {
        payload.url = url.trim();
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
      setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: '🛑 Đã dừng tiến trình & dọn dẹp sạch toàn bộ các chunk tạm!', type: 'amber' }]);
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
      return <span className="tag-badge emerald" style={{ fontSize: '10px' }}>✓ XONG</span>;
    }
    if (p.translated_dialogues > 0 || p.status === 'TRANSLATED') {
      return <span className="tag-badge purple" style={{ fontSize: '10px' }}>ĐÃ DỊCH</span>;
    }
    return <span className="tag-badge amber" style={{ fontSize: '10px' }}>MỚI TẢI</span>;
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
  const remainH = Math.max(10, 100 - maskHeight);
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
                
                {/* Mode Toggles when final video is available */}
                {(selectedProject?.has_final || selectedProject?.status === 'COMPLETED') && (
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', marginLeft: '6px' }}>
                    <button
                      type="button"
                      onClick={() => setVideoViewMode('final')}
                      style={{
                        padding: '3px 8px',
                        fontSize: '11px',
                        fontWeight: 700,
                        borderRadius: '4px',
                        cursor: 'pointer',
                        background: videoViewMode === 'final' ? '#2563eb' : '#f1f5f9',
                        color: videoViewMode === 'final' ? '#ffffff' : '#475569',
                        border: '1px solid ' + (videoViewMode === 'final' ? '#2563eb' : '#cbd5e1')
                      }}
                      title="Xem video đã thuyết minh tiếng Việt và sub karaoke"
                    >
                      🎬 Thuyết Minh
                    </button>
                    <button
                      type="button"
                      onClick={() => setVideoViewMode('raw')}
                      style={{
                        padding: '3px 8px',
                        fontSize: '11px',
                        fontWeight: 700,
                        borderRadius: '4px',
                        cursor: 'pointer',
                        background: videoViewMode === 'raw' ? '#475569' : '#f1f5f9',
                        color: videoViewMode === 'raw' ? '#ffffff' : '#475569',
                        border: '1px solid ' + (videoViewMode === 'raw' ? '#475569' : '#cbd5e1')
                      }}
                      title="Xem video tiếng Trung gốc"
                    >
                      📹 Gốc
                    </button>
                  </div>
                )}
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
                    onClick={(e) => handleResetProject(e, selectedProject.id, selectedProject.title)}
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
                    title="Reset về lúc mới tải xong"
                  >
                    <RotateCcw size={11} /> Reset
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

            {/* Row 2: Subtitle Elevation & Cắt Sub 9:16 Controls */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap', background: '#f8fafc', padding: '6px 10px', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
              {/* Subtitle Elevation */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <span style={{ fontSize: '11px', fontWeight: 700, color: '#334155' }}>Cao Độ Sub:</span>
                <button
                  type="button"
                  onClick={() => setSubBottomOffset(prev => Math.min(85, prev + 1))}
                  title="Nâng phụ đề lên (Phím ↑)"
                  style={{ background: '#ffffff', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <ArrowUp size={11} color="#2563eb" />
                </button>
                <button
                  type="button"
                  onClick={() => setSubBottomOffset(prev => Math.max(0, prev - 1))}
                  title="Hạ phụ đề xuống (Phím ↓)"
                  style={{ background: '#ffffff', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <ArrowDown size={11} color="#2563eb" />
                </button>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
                  <input
                    type="number"
                    min="0"
                    max="85"
                    value={subBottomOffset}
                    onChange={(e) => setSubBottomOffset(Math.max(0, Math.min(85, parseInt(e.target.value, 10) || 0)))}
                    style={{ width: '40px', padding: '1px 3px', border: '1.5px solid #2563eb', borderRadius: '4px', fontSize: '11px', fontWeight: 800, textAlign: 'center', color: '#0f172a' }}
                    title="Nhập trực tiếp % khoảng cách từ đáy (0% - 85%)"
                  />
                  <span style={{ fontSize: '11px', fontWeight: 800, color: '#0f172a' }}>%</span>
                </div>

                {/* Quick Presets */}
                <div style={{ display: 'inline-flex', gap: '2px', marginLeft: '2px' }}>
                  {[5, 8, 12, 16, 20].map(p => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => setSubBottomOffset(p)}
                      style={{
                        fontSize: '9.5px',
                        fontWeight: 700,
                        padding: '1px 4px',
                        borderRadius: '3px',
                        border: '1px solid',
                        borderColor: subBottomOffset === p ? '#2563eb' : '#cbd5e1',
                        background: subBottomOffset === p ? '#2563eb' : '#ffffff',
                        color: subBottomOffset === p ? '#ffffff' : '#475569',
                        cursor: 'pointer'
                      }}
                      title={`Đặt nhanh cao độ ${p}%`}
                    >
                      {p}%
                    </button>
                  ))}
                </div>
              </div>

              {/* Cỡ Chữ Phụ Đề */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <span style={{ fontSize: '11px', fontWeight: 700, color: '#334155' }}>Cỡ Chữ:</span>
                <button
                  type="button"
                  onClick={() => setSubFontSize(prev => Math.max(9, prev - 1))}
                  title="Giảm cỡ chữ"
                  style={{ background: '#ffffff', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <Minus size={11} color="#2563eb" />
                </button>
                <button
                  type="button"
                  onClick={() => setSubFontSize(prev => Math.min(24, prev + 1))}
                  title="Tăng cỡ chữ"
                  style={{ background: '#ffffff', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <Plus size={11} color="#2563eb" />
                </button>
                <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', fontWeight: 800, color: '#0f172a', minWidth: '30px', textAlign: 'center' }}>
                  {subFontSize}px
                </span>
              </div>


              {/* Chiều Cao Vùng Mờ Sub (Cao Nền) */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', background: '#eff6ff', padding: '2px 8px', borderRadius: '5px', border: '1.5px solid #2563eb' }}>
                <span style={{ fontSize: '11px', fontWeight: 800, color: '#1e40af' }}>Cao Nền:</span>
                <button
                  type="button"
                  onClick={() => setBlurHeight(prev => Math.max(8, prev - 1))}
                  title="Giảm chiều cao dải mờ che chữ"
                  style={{ background: '#ffffff', border: '1px solid #93c5fd', borderRadius: '3px', padding: '1px 5px', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <Minus size={11} color="#2563eb" />
                </button>
                <span style={{ fontSize: '11.5px', fontFamily: 'var(--font-mono)', fontWeight: 900, color: '#1d4ed8', minWidth: '32px', textAlign: 'center' }}>
                  {blurHeight}%
                </span>
                <button
                  type="button"
                  onClick={() => setBlurHeight(prev => Math.min(30, prev + 1))}
                  title="Tăng chiều cao dải mờ che chữ"
                  style={{ background: '#ffffff', border: '1px solid #93c5fd', borderRadius: '3px', padding: '1px 5px', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                >
                  <Plus size={11} color="#2563eb" />
                </button>
              </div>

              {/* Cắt Sub & Chuẩn Tỷ Lệ YouTube Controls */}
              {hasMask ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                  {/* Selector Tỷ lệ Chuẩn */}
                  <div style={{ display: 'inline-flex', borderRadius: '4px', overflow: 'hidden', border: '1.5px solid #2563eb' }}>
                    <button
                      type="button"
                      onClick={() => setCropRatio('16:9')}
                      style={{
                        padding: '3px 8px',
                        fontSize: '11px',
                        fontWeight: 800,
                        border: 'none',
                        cursor: 'pointer',
                        background: cropRatio === '16:9' ? '#2563eb' : '#ffffff',
                        color: cropRatio === '16:9' ? '#ffffff' : '#2563eb'
                      }}
                      title="Tỷ lệ 16:9 YouTube Chuẩn"
                    >
                      🎬 16:9 YouTube Chuẩn
                    </button>
                    <button
                      type="button"
                      onClick={() => setCropRatio('9:16')}
                      style={{
                        padding: '3px 8px',
                        fontSize: '11px',
                        fontWeight: 800,
                        border: 'none',
                        borderLeft: '1px solid #2563eb',
                        cursor: 'pointer',
                        background: cropRatio === '9:16' ? '#2563eb' : '#ffffff',
                        color: cropRatio === '9:16' ? '#ffffff' : '#2563eb'
                      }}
                      title="Tỷ lệ 9:16 Dọc dành cho YouTube Shorts / TikTok"
                    >
                      📱 9:16 Shorts
                    </button>
                  </div>

                  {/* Độ cao cắt sub đáy */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px', background: '#eff6ff', padding: '3px 8px', borderRadius: '5px', border: '1px solid #93c5fd' }}>
                    <span style={{ fontSize: '11px', fontWeight: 800, color: '#1e40af', display: 'flex', alignItems: 'center', gap: '3px' }}>
                      ✂ Cắt Đáy:
                    </span>
                    <button
                      type="button"
                      onClick={() => setMaskHeight(prev => Math.max(1, prev - 1))}
                      title="Giảm độ cao cắt sub"
                      style={{ background: '#ffffff', border: '1px solid #93c5fd', borderRadius: '3px', padding: '1px 6px', cursor: 'pointer', fontSize: '11px', fontWeight: 800, color: '#1e40af' }}
                    >
                      -
                    </button>
                    <span style={{ fontSize: '11.5px', fontFamily: 'var(--font-mono)', fontWeight: 900, color: '#2563eb', minWidth: '28px', textAlign: 'center' }}>
                      {maskHeight}%
                    </span>
                    <button
                      type="button"
                      onClick={() => setMaskHeight(prev => Math.min(35, prev + 1))}
                      title="Tăng độ cao cắt sub"
                      style={{ background: '#ffffff', border: '1px solid #93c5fd', borderRadius: '3px', padding: '1px 6px', cursor: 'pointer', fontSize: '11px', fontWeight: 800, color: '#1e40af' }}
                    >
                      +
                    </button>

                    <input
                      type="range"
                      min={1}
                      max={30}
                      value={maskHeight}
                      onChange={(e) => setMaskHeight(parseInt(e.target.value, 10))}
                      title="Kéo chọn % chiều cao cắt sub đáy"
                      style={{ width: '65px', height: '4px', accentColor: '#2563eb', cursor: 'pointer' }}
                    />
                  </div>

                  {/* Preset Buttons */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                    {[
                      { label: '5%', val: 5 },
                      { label: '8% (Nhẹ)', val: 8 },
                      { label: '12% (Chuẩn)', val: 12 },
                      { label: '16%', val: 16 }
                    ].map(p => (
                      <button
                        key={p.val}
                        type="button"
                        onClick={() => setMaskHeight(p.val)}
                        style={{
                          fontSize: '10px',
                          fontWeight: 700,
                          padding: '2px 5px',
                          borderRadius: '3px',
                          border: '1px solid',
                          borderColor: maskHeight === p.val ? '#0284c7' : '#cbd5e1',
                          background: maskHeight === p.val ? '#0284c7' : '#ffffff',
                          color: maskHeight === p.val ? '#ffffff' : '#475569',
                          cursor: 'pointer'
                        }}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>

                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setHasMask(false)}
                    title="Tắt cắt sub & dải mờ"
                    style={{ fontSize: '10.5px', padding: '2px 6px', color: '#64748b' }}
                  >
                    Tắt Cắt
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    setHasMask(true);
                    setMaskHeight(8);
                    setCropRatio('16:9');
                  }}
                  style={{ fontSize: '11px', padding: '3px 10px', background: '#0284c7', color: '#ffffff', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '4px' }}
                >
                  <Plus size={11} /> + Bật Cắt Sub 16:9 YouTube
                </button>
              )}
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
                cursor: interactionMode === 'crop-bottom' ? 'ns-resize' : 'default',
                boxShadow: '0 2px 10px rgba(0, 0, 0, 0.25)'
              }}
            >
              {selectedProject ? (
                <video
                  ref={videoRef}
                  key={`${selectedProject.id}_${videoViewMode}`}
                  preload="metadata"
                  playsInline
                  onTimeUpdate={handleTimeUpdate}
                  onLoadedMetadata={handleLoadedMetadata}
                  onEnded={() => setIsPlaying(false)}
                  onClick={togglePlayPause}
                  style={{ width: '100%', height: '100%', objectFit: 'fill', display: 'block', cursor: 'pointer' }}
                  src={`/api/v1/projects/${selectedProject.id}/video?type=${videoViewMode}`}
                />
              ) : (
                <div style={{ color: 'var(--text-dim)', textAlign: 'center', pointerEvents: 'none' }}>
                  <Film size={38} style={{ opacity: 0.3, marginBottom: '6px' }} />
                  <div style={{ fontSize: '12px' }}>Chưa chọn video nào. Hãy chọn ở danh sách bên phải!</div>
                </div>
              )}

              {/* CẮT SUB & TỰ ĐỘNG CÂN TỶ LỆ CHUẨN YOUTUBE PREVIEW OVERLAYS */}
              {hasMask && (
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

                  {/* 3. VÙNG CẮT SUB Ở MÉP ĐÁY (ĐỎ CẢNH BÁO) */}
                  <div
                    style={{
                      position: 'absolute',
                      top: `${remainH}%`,
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
                      top: `calc(${remainH}% - 8px)`,
                      left: 0,
                      right: 0,
                      height: '16px',
                      cursor: 'ns-resize',
                      zIndex: 35
                    }}
                    title="Kéo mép này lên/xuống để chọn độ cao cắt sub đáy"
                  />

                  {/* 5. KHUNG THÀNH PHẨM YOUTUBE / SHORTS */}
                  <div
                    style={{
                      position: 'absolute',
                      top: 0,
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

              {/* DYNAMIC SUBTITLE PREVIEW (ĐỒNG BỘ 1:1 VỚI BẢN RENDER VIDEO THÀNH PHẨM) */}
              {hasMask ? (
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
              gap: '10px'
            }}>
              <button
                onClick={togglePlayPause}
                style={{
                  width: '26px',
                  height: '26px',
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
                {isPlaying ? <Pause size={12} /> : <Play size={12} style={{ marginLeft: '1px' }} />}
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
                  title="Dừng ngay lập tức và dọn dẹp sạch sẽ các chunk tạm để không bị lẫn"
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
                  🛑 DỪNG & XÓA
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
                  background: 'linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%)',
                  boxShadow: '0 4px 12px rgba(37, 99, 235, 0.25)'
                }}
              >
                <Zap size={14} /> BẮT ĐẦU TỰ ĐỘNG HÓA 100% (DỊCH ➔ TTS ➔ RENDER)
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
                  return (
                    <div
                      key={p.id}
                      onClick={() => setSelectedProject(p)}
                      style={{
                        padding: '8px 12px',
                        background: isSelected ? '#eff6ff' : '#f8fafc',
                        border: isSelected ? '1.5px solid #2563eb' : '1px solid #e2e8f0',
                        borderRadius: '8px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '8px',
                        transition: 'all 0.15s ease'
                      }}
                      onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = '#edf2f7'; }}
                      onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = '#f8fafc'; }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          fontSize: '12px',
                          fontWeight: isSelected ? 800 : 700,
                          color: isSelected ? '#2563eb' : '#0f172a',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis'
                        }}>
                          #{p.id} - {p.title}
                        </div>
                        <div style={{ display: 'flex', gap: '8px', fontSize: '11px', color: '#64748b', marginTop: '2px' }}>
                          <span>⏱ {formatDuration(p.duration)}</span>
                          <span>💬 {p.total_dialogues} câu</span>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                        {getStatusBadge(p)}
                        <button
                          type="button"
                          onClick={(e) => handleResetProject(e, p.id, p.title)}
                          style={{
                            background: '#fffbeb',
                            border: '1px solid #fcd34d',
                            padding: '3px 6px',
                            cursor: 'pointer',
                            color: '#d97706',
                            borderRadius: '4px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '3px',
                            fontSize: '10.5px',
                            fontWeight: 700,
                            transition: 'all 0.15s ease'
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.background = '#fef3c7'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = '#fffbeb'; }}
                          title="Reset về lúc mới tải xong (giữ lại video gốc, xóa câu thoại/audio/video render để làm lại)"
                        >
                          <RotateCcw size={11} /> Reset
                        </button>
                        <button
                          type="button"
                          onClick={(e) => handleDeleteProject(e, p.id, p.title)}
                          style={{
                            background: 'none',
                            border: 'none',
                            padding: '4px',
                            cursor: 'pointer',
                            color: '#94a3b8',
                            borderRadius: '4px',
                            display: 'flex',
                            alignItems: 'center',
                            transition: 'all 0.15s ease'
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.background = '#fee2e2'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.background = 'none'; }}
                          title="Xóa vĩnh viễn video này và toàn bộ dữ liệu/file liên quan"
                        >
                          <Trash2 size={13} />
                        </button>
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
