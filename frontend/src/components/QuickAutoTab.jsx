import React, { useState, useEffect, useRef } from 'react';
import { 
  Zap, Play, Download, Sparkles, Volume2, Film, CheckCircle2, 
  AlertTriangle, RefreshCw, RotateCcw, Terminal, Eye, Sliders, Music, ShieldCheck
} from 'lucide-react';
import { AIREAD_GENRES, DEFAULT_GENRE } from '../constants/genres';
import { ALL_VOICES } from '../constants/voices';

export default function QuickAutoTab({ onNavigateStudio }) {
  const [url, setUrl] = useState('');
  const [genre, setGenre] = useState(DEFAULT_GENRE);
  const [voiceCode, setVoiceCode] = useState('BV074_streaming');
  const [quality, setQuality] = useState('480p');
  const [marginV, setMarginV] = useState(45);
  const [backdropOpacity, setBackdropOpacity] = useState('99');
  const [channelName, setChannelName] = useState('@Mắt Thần Review');
  const [logoPosition, setLogoPosition] = useState('top_left');
  const [karaokeColor, setKaraokeColor] = useState('&H0000D7FF');

  // Task & Execution State
  const [taskId, setTaskId] = useState(null);
  const [isRunning, setIsRunning] = useState(false);
  const [taskStatus, setTaskStatus] = useState(null);
  const [progress, setProgress] = useState(0);
  const [currentStep, setCurrentStep] = useState(1);
  const [logs, setLogs] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Audio Preview State
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewAudioUrl, setPreviewAudioUrl] = useState(null);

  // Poll Task Status
  useEffect(() => {
    let interval = null;
    if (isRunning && taskId) {
      interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/v1/pipeline/status/${taskId}`);
          if (!res.ok) return;
          const data = await res.json();
          setTaskStatus(data.status);
          setProgress(data.progress || 0);
          setCurrentStep(data.step || 1);
          if (data.logs) setLogs(data.logs);

          if (data.status === 'completed') {
            setIsRunning(false);
            setResult(data.result);
            clearInterval(interval);
          } else if (data.status === 'failed') {
            setIsRunning(false);
            setError(data.error || 'Quy trình thất bại');
            clearInterval(interval);
          }
        } catch (e) {
          console.error(e);
        }
      }, 1200);
    }
    return () => clearInterval(interval);
  }, [isRunning, taskId]);

  const handleStartFullAuto = async () => {
    if (!url.trim()) {
      alert('Vui lòng nhập đường link Video Bilibili hoặc YouTube!');
      return;
    }

    setIsRunning(true);
    setProgress(2);
    setCurrentStep(1);
    setLogs([]);
    setResult(null);
    setError(null);

    try {
      const res = await fetch('/api/v1/pipeline/full-auto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: url.trim(),
          quality: quality,
          source_language: 'zh',
          genre: genre,
          provider: 'gemini',
          voice_code: voiceCode,
          margin_v: marginV,
          backdrop_opacity_hex: backdropOpacity,
          karaoke_highlight_color: karaokeColor,
          channel_name: channelName,
          channel_opacity: 0.35,
          logo_position: logoPosition,
          logo_size: 120
        })
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Không thể khởi chạy quy trình tự động');
      }

      const data = await res.json();
      setTaskId(data.task_id);
    } catch (e) {
      setIsRunning(false);
      setError(e.message);
      alert(`Lỗi: ${e.message}`);
    }
  };

  const handleTestVoice = async () => {
    setIsPreviewLoading(true);
    setPreviewAudioUrl(null);
    try {
      const sampleText = genre === 'cophong' 
        ? "Đạo hữu xin dừng bước! Hôm nay ta sẽ review cho chư vị một tuyệt phẩm tu tiên chấn động càn khôn."
        : "Chào mừng các bạn đã quay trở lại với Mắt Thần Review, hôm nay chúng ta cùng theo dõi diễn biến tiếp theo nhé.";

      const res = await fetch('/api/v1/tts/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: sampleText,
          voice_code: voiceCode,
          apply_mastering: true,
          playback_speed: 1.0
        })
      });

      if (!res.ok) throw new Error('Không thể tạo giọng thử nghiệm');
      const data = await res.json();
      if (data.audio_base64) {
        const audioSrc = `data:audio/mp3;base64,${data.audio_base64}`;
        setPreviewAudioUrl(audioSrc);
        const audio = new Audio(audioSrc);
        audio.play();
      }
    } catch (e) {
      alert(`Lỗi nghe thử giọng: ${e.message}`);
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const handleResetResult = async () => {
    if (!result?.project_id) return;
    if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN RESET VIDEO NÀY VỀ BAN ĐẦU?\n\nThao tác này sẽ:\n✓ GIỮ NGUYÊN FILE VIDEO GỐC ĐÃ TẢI VỀ\n✗ Xóa toàn bộ câu thoại, bản dịch tiếng Việt\n✗ Xóa toàn bộ audio thuyết minh và video render thành phẩm\n\nVideo sẽ trở về trạng thái như lúc mới tải xong để bạn có thể cấu hình và chạy lại!`)) return;

    try {
      const res = await fetch(`/api/v1/projects/${result.project_id}/reset`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        alert(data.message || 'Đã reset video về lúc mới tải xong thành công!');
        setResult(null);
        setTaskId(null);
        setProgress(0);
        setCurrentStep(1);
        setLogs(prev => [...prev, { time: new Date().toLocaleTimeString(), text: '🔄 Đã reset video về lúc mới tải xong (giữ video gốc, dọn sạch thoại/audio/video render)!', type: 'amber' }]);
      } else {
        alert(`Lỗi: ${data.detail || 'Không thể reset'}`);
      }
    } catch (err) {
      alert(`Lỗi: ${err.message}`);
    }
  };

  const steps = [
    { num: 1, title: 'Tải & Trích Audio', desc: 'Trích xuất 16kHz PCM' },
    { num: 2, title: 'Bóc Tách Thoại (CapCut Cloud STT)', desc: 'Chính xác từng mili-giây' },
    { num: 3, title: 'Dịch Thuật AIREAD', desc: 'Lọc thực thể & dịch lô' },
    { num: 4, title: 'Lồng Tiếng TikTok TTS', desc: 'Fit chuẩn timeline Smart Headroom' },
    { num: 5, title: 'Xuất Bản Video 1-Pass', desc: 'Karaoke + Mix BGM + Logo' },
  ];

  return (
    <div className="tab-container" style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* Hero Banner */}
      <div className="glass-panel" style={{
        background: 'linear-gradient(135deg, rgba(16,22,45,0.85) 0%, rgba(30,20,60,0.75) 100%)',
        border: '1px solid rgba(0, 242, 254, 0.25)',
        padding: '24px 28px',
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-neon)'
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
              <span className="tag-badge cyan" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                ⚡ 1-CLICK STUDIO
              </span>
              <span className="tag-badge emerald" style={{ padding: '4px 10px', fontSize: '11px', fontWeight: 800 }}>
                100% AUTOMATED
              </span>
            </div>
            <h1 style={{ fontSize: '22px', fontWeight: 800, color: 'var(--text-main)', marginBottom: '6px' }}>
              Lồng Tiếng Video Tự Động 100%
            </h1>
            <p style={{ color: 'var(--text-sub)', fontSize: '13px', maxWidth: '650px' }}>
              Dán URL video ➔ Hệ thống tự động: Bóc tách thoại ➔ Dịch AIREAD chuẩn xưng hô ➔ Quét sạch Hán ➔ Lồng tiếng TikTok TTS chuẩn khung giờ ➔ Phủ hộp nền che phụ đề cũ & đè Karaoke mới.
            </p>
          </div>

          <button 
            className="btn btn-primary"
            onClick={handleStartFullAuto}
            disabled={isRunning}
            style={{
              padding: '14px 28px',
              fontSize: '15px',
              fontWeight: 800,
              background: 'var(--grad-cyan)',
              boxShadow: '0 0 25px rgba(0, 242, 254, 0.4)',
              color: '#070913'
            }}
          >
            {isRunning ? (
              <>
                <RefreshCw size={18} className="animate-spin" /> Đang Chạy Tự Động ({progress}%)...
              </>
            ) : (
              <>
                <Zap size={18} /> BẮT ĐẦU TỰ ĐỘNG HÓA 100%
              </>
            )}
          </button>
        </div>
      </div>

      {/* Grid: Left Config & Canvas Preview | Right Progress & Result */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: '24px' }}>
        
        {/* Left Column: Input & Live Canvas Visual Box */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Card 1: Input URL & Quality */}
          <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, marginBottom: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Film size={16} color="var(--cyan)" /> 1. Nhập Video Nguồn
            </h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
                  Đường dẫn Video (Bilibili / YouTube)
                </label>
                <input
                  type="text"
                  placeholder="https://www.bilibili.com/video/BV... hoặc https://youtu.be/..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={isRunning}
                  style={{
                    width: '100%',
                    padding: '12px 14px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-main)',
                    fontSize: '13px'
                  }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
                    Chất lượng Video
                  </label>
                  <select 
                    value={quality} 
                    onChange={(e) => setQuality(e.target.value)}
                    disabled={isRunning}
                    style={{ width: '100%', padding: '10px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)' }}
                  >
                    <option value="1080p">🌟 1080p Full HD (60fps/30fps)</option>
                    <option value="720p">⚡ 720p HD (60fps/30fps)</option>
                    <option value="480p">🌾 480p SD (Tiêu chuẩn nhẹ)</option>
                    <option value="360p">💾 360p (Tiết kiệm dung lượng)</option>
                    <option value="audio">🎵 Chỉ tải Audio 16kHz</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
                    Thể loại AIREAD
                  </label>
                  <select 
                    value={genre} 
                    onChange={(e) => setGenre(e.target.value)}
                    disabled={isRunning}
                    style={{ width: '100%', padding: '10px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)' }}
                  >
                    {AIREAD_GENRES.map((g) => (
                      <option key={g.code} value={g.code}>
                        {g.icon} {g.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          </div>

          {/* Card 2: Voice & Audio Tuning */}
          <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h3 style={{ fontSize: '15px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Volume2 size={16} color="var(--purple)" /> 2. Giọng Đọc TikTok TTS
              </h3>

              <button
                className="btn btn-secondary btn-sm"
                onClick={handleTestVoice}
                disabled={isPreviewLoading}
                style={{ fontSize: '12px', padding: '6px 12px' }}
              >
                {isPreviewLoading ? <RefreshCw size={13} className="animate-spin" /> : <Play size={13} />} Nghe Thử Mẫu
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
                  Giọng Đọc
                </label>
                <select 
                  value={voiceCode} 
                  onChange={(e) => setVoiceCode(e.target.value)}
                  disabled={isRunning}
                  style={{ width: '100%', padding: '10px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)' }}
                >
                  {ALL_VOICES.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
                  Đồng Bộ Timeline
                </label>
                <div style={{ padding: '10px', background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: 'var(--radius-sm)', fontSize: '12px', color: 'var(--emerald)' }}>
                  ✔ Smart Headroom (Khớp 100% không lệch)
                </div>
              </div>
            </div>
          </div>

          {/* Card 3: Visual Subtitle Target Box & Logo Customizer */}
          <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, marginBottom: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Sliders size={16} color="var(--amber)" /> 3. Vùng Che Phụ Đề Gốc & Vị Trí Sub Mới
            </h3>

            {/* Visual Canvas Simulator */}
            <div style={{
              position: 'relative',
              width: '100%',
              height: '200px',
              background: '#070913',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              overflow: 'hidden',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              marginBottom: '16px'
            }}>
              {/* Background Mock Video */}
              <div style={{ position: 'absolute', inset: 0, opacity: 0.25, background: 'radial-gradient(circle at center, #1e293b 0%, #0f172a 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Film size={48} color="#64748b" />
              </div>

              {/* Mock Logo */}
              <div style={{
                position: 'absolute',
                top: logoPosition.includes('top') ? '12px' : 'auto',
                bottom: logoPosition.includes('bottom') ? '12px' : 'auto',
                left: logoPosition.includes('left') ? '12px' : 'auto',
                right: logoPosition.includes('right') ? '12px' : 'auto',
                padding: '4px 8px',
                background: 'rgba(233,64,87,0.85)',
                borderRadius: '4px',
                fontSize: '10px',
                fontWeight: 800,
                color: '#fff',
                boxShadow: '0 2px 8px rgba(0,0,0,0.5)'
              }}>
                LOGO REVIEW
              </div>

              {/* Mock Watermark */}
              <div style={{
                position: 'absolute',
                top: '12px',
                right: '12px',
                fontSize: '11px',
                fontWeight: 700,
                color: 'rgba(255,255,255,0.45)',
                letterSpacing: '0.5px'
              }}>
                {channelName}
              </div>

              {/* Frosted Subtitle Target Box Simulator */}
              <div style={{
                position: 'absolute',
                bottom: `${marginV}px`,
                left: '20px',
                right: '20px',
                padding: '8px 14px',
                background: `rgba(0, 0, 0, ${parseInt(backdropOpacity, 16) / 255})`,
                backdropFilter: 'blur(4px)',
                border: '1px dashed var(--cyan)',
                borderRadius: '6px',
                textAlign: 'center',
                boxShadow: '0 4px 15px rgba(0,0,0,0.6)'
              }}>
                <div style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '2px' }}>
                  [VÙNG CHE SUB TRUNG GỐC & HIỆN KARAOKE MỚI]
                </div>
                <div style={{ fontSize: '13px', fontWeight: 800, color: '#fff' }}>
                  <span style={{ color: '#00D7FF' }}>Hôm nay</span> chúng ta cùng theo dõi diễn biến...
                </div>
              </div>
            </div>

            {/* Position Sliders */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <label style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Độ cao Sub (Margin V):</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <input
                      type="number"
                      min="10"
                      max="200"
                      value={marginV}
                      onChange={(e) => setMarginV(parseInt(e.target.value, 10) || 45)}
                      style={{ width: '55px', padding: '2px 6px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: '4px', color: 'var(--cyan)', fontSize: '11px', textAlign: 'center', fontWeight: 700 }}
                    />
                    <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>px</span>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '6px' }}>
                  {[
                    { v: 30, l: '30px' },
                    { v: 45, l: '45px (Chuẩn)' },
                    { v: 60, l: '60px' },
                    { v: 80, l: '80px' }
                  ].map((lvl) => (
                    <button
                      key={lvl.v}
                      type="button"
                      onClick={() => setMarginV(lvl.v)}
                      style={{
                        flex: 1,
                        padding: '6px 4px',
                        fontSize: '11px',
                        fontWeight: 700,
                        borderRadius: '6px',
                        cursor: 'pointer',
                        background: marginV === lvl.v ? 'rgba(0, 242, 254, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                        border: marginV === lvl.v ? '1px solid var(--cyan)' : '1px solid var(--border)',
                        color: marginV === lvl.v ? 'var(--cyan)' : 'var(--text-sub)',
                        transition: 'all 0.15s ease'
                      }}
                    >
                      {lvl.l}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                  Vị Trí Logo Góc:
                </label>
                <select 
                  value={logoPosition} 
                  onChange={(e) => setLogoPosition(e.target.value)}
                  style={{ width: '100%', padding: '6px 10px', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-main)', fontSize: '12px' }}
                >
                  <option value="top_left">Góc Trên Trái (Mặc định)</option>
                  <option value="top_right">Góc Trên Phải</option>
                  <option value="bottom_left">Góc Dưới Trái</option>
                  <option value="bottom_right">Góc Dưới Phải</option>
                </select>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Progress Radar, Logs & Final Video Result */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Stepper Progress */}
          <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, marginBottom: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <CheckCircle2 size={16} color="var(--emerald)" /> Tiến Trình Xử Lý Tự Động
            </h3>

            {/* Progress Bar */}
            <div style={{ width: '100%', height: '8px', background: 'var(--bg-input)', borderRadius: '4px', overflow: 'hidden', marginBottom: '16px' }}>
              <div style={{
                width: `${progress}%`,
                height: '100%',
                background: 'var(--grad-cyan)',
                transition: 'width 0.4s ease',
                boxShadow: '0 0 12px rgba(0, 242, 254, 0.6)'
              }} />
            </div>

            {/* Steps List */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {steps.map((s) => {
                const isCurrent = currentStep === s.num && isRunning;
                const isDone = currentStep > s.num || (!isRunning && result);
                return (
                  <div key={s.num} style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '10px 14px',
                    background: isCurrent ? 'rgba(0, 242, 254, 0.08)' : 'rgba(255, 255, 255, 0.02)',
                    border: isCurrent ? '1px solid rgba(0, 242, 254, 0.3)' : '1px solid transparent',
                    borderRadius: 'var(--radius-sm)'
                  }}>
                    <div style={{
                      width: '26px',
                      height: '26px',
                      borderRadius: '50%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '12px',
                      fontWeight: 800,
                      background: isDone ? 'var(--emerald)' : isCurrent ? 'var(--cyan)' : 'var(--bg-input)',
                      color: isDone || isCurrent ? '#070913' : 'var(--text-dim)'
                    }}>
                      {isDone ? '✓' : s.num}
                    </div>

                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '13px', fontWeight: 700, color: isCurrent ? 'var(--cyan)' : isDone ? 'var(--text-main)' : 'var(--text-dim)' }}>
                        {s.title}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-dim)' }}>
                        {s.desc}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Terminal Logs Window */}
          <div className="glass-panel" style={{ padding: '16px', borderRadius: 'var(--radius-md)', flex: 1, minHeight: '180px', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', fontSize: '12px', color: 'var(--text-sub)', fontWeight: 700 }}>
              <Terminal size={14} color="var(--cyan)" /> Live Terminal Logs
            </div>

            <div style={{
              flex: 1,
              maxHeight: '180px',
              overflowY: 'auto',
              background: '#050711',
              padding: '10px 12px',
              borderRadius: '6px',
              fontFamily: 'var(--font-mono)',
              fontSize: '11px',
              lineHeight: 1.6,
              color: '#94a3b8'
            }}>
              {logs.length === 0 ? (
                <div style={{ color: '#475569' }}>Hệ thống đang sẵn sàng. Nhấn [BẮT ĐẦU TỰ ĐỘNG HÓA] để chạy...</div>
              ) : (
                logs.map((l, idx) => (
                  <div key={idx} style={{
                    color: l.type === 'emerald' ? '#34d399' : l.type === 'cyan' ? '#38bdf8' : l.type === 'rose' ? '#f43f5e' : l.type === 'amber' ? '#fbbf24' : '#cbd5e1'
                  }}>
                    [{l.time}] {l.text}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Result Action Card */}
          {result && (
            <div className="glass-panel" style={{
              padding: '20px',
              borderRadius: 'var(--radius-md)',
              background: 'linear-gradient(135deg, rgba(16,185,129,0.15) 0%, rgba(16,22,45,0.9) 100%)',
              border: '1px solid rgba(16,185,129,0.4)',
              boxShadow: '0 0 25px rgba(16,185,129,0.2)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                <CheckCircle2 size={22} color="var(--emerald)" />
                <div>
                  <h4 style={{ fontSize: '15px', fontWeight: 800, color: 'var(--text-main)' }}>
                    Xuất Bản Thành Công!
                  </h4>
                  <div style={{ fontSize: '12px', color: 'var(--text-sub)' }}>
                    Video đã được lồng tiếng hoàn chỉnh và sẵn sàng để tải về.
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', gap: '10px', marginTop: '14px', flexWrap: 'wrap' }}>
                {result.final_video_path && (
                  <a
                    href={`/api/v1/projects/download-video/${result.project_id}`}
                    download
                    className="btn btn-primary"
                    style={{ flex: 1, textDecoration: 'none', textAlign: 'center', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
                  >
                    <Download size={14} /> Tải Video Hoàn Chỉnh (.mp4)
                  </a>
                )}

                <button
                  className="btn btn-secondary"
                  onClick={() => onNavigateStudio && onNavigateStudio('translation', result.project_id)}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <Eye size={14} /> Mở Trong Studio Biên Tập
                </button>

                <button
                  className="btn btn-secondary"
                  onClick={handleResetResult}
                  title="Reset về lúc mới tải xong (giữ lại video gốc, dọn sạch thoại/audio/video render để làm lại từ đầu)"
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--amber)', borderColor: 'rgba(245, 158, 11, 0.4)' }}
                >
                  <RotateCcw size={14} /> Reset Về Ban Đầu
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
