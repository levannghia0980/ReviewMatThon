import React, { useState, useEffect } from 'react';
import { 
  FolderDown, 
  Database, 
  Cpu, 
  ShieldCheck, 
  Zap, 
  KeyRound, 
  CloudLightning, 
  CheckCircle2, 
  AlertCircle, 
  Loader2, 
  Eye, 
  EyeOff, 
  Save, 
  ExternalLink,
  Sparkles,
  Bot,
  Sliders,
  Layers,
  Volume2,
  RefreshCw,
  Gauge,
  Flame,
  Rocket,
  Globe
} from 'lucide-react';

export default function SettingsTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingGroq, setTestingGroq] = useState(false);
  const [testingGemini, setTestingGemini] = useState(false);
  
  // API Keys Reveal State
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showGroqKey, setShowGroqKey] = useState(false);
  const [showOpenRouterKey, setShowOpenRouterKey] = useState(false);

  // Form State
  const [geminiMode, setGeminiMode] = useState('web'); // 'web' hoặc 'api'
  const [geminiKey, setGeminiKey] = useState('');
  const [geminiModel, setGeminiModel] = useState('gemini-3.1-flash-lite');
  const [geminiWebStatus, setGeminiWebStatus] = useState({ logged_in: false, message: 'Đang kiểm tra...' });
  const [geminiWebModel, setGeminiWebModel] = useState('flash'); // 'flash' hoặc 'pro'
  const [geminiWebThinking, setGeminiWebThinking] = useState('medium'); // 'low', 'medium', 'high'
  const [testingGeminiWeb, setTestingGeminiWeb] = useState(false);
  const [geminiWebTestResult, setGeminiWebTestResult] = useState(null);
  
  const [groqKey, setGroqKey] = useState('');
  const [groqModel, setGroqModel] = useState('whisper-large-v3');
  const [asrEngine, setAsrEngine] = useState('capcut');
  const [whisperSize, setWhisperSize] = useState('base');

  const [openrouterKey, setOpenrouterKey] = useState('');
  
  // Translation Batch Settings
  const [translationMaxChars, setTranslationMaxChars] = useState(50000);
  const [translationBatchSize, setTranslationBatchSize] = useState(500);
  const [translationTemperature, setTranslationTemperature] = useState(0.85);

  const handleModeSwitch = (mode) => {
    setGeminiMode(mode);
    fetch('/api/v1/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ translation_provider: mode === 'web' ? 'gemini_web' : 'gemini' })
    })
      .then(res => res.json())
      .then(data => {
        if (data.settings) {
          window.dispatchEvent(new CustomEvent('settings-updated', { detail: data.settings }));
        }
      })
      .catch(() => {});
  };
  
  // TTS Engine & Concurrency (CapCut vs TikTok, Up to 128 threads)
  const [ttsEngine, setTtsEngine] = useState('capcut');
  const [ttsThreads, setTtsThreads] = useState(128);
  const [capcutCookie, setCapcutCookie] = useState('');
  const [tiktokSessionId, setTiktokSessionId] = useState('410bfa37bdc185e1c6da82e1afb48409');

  // Bilibili Cookie & Downloader Settings (Không proxy, 16 luồng song song)
  const [bilibiliCookieFile, setBilibiliCookieFile] = useState('');
  const [hasBilibiliCookie, setHasBilibiliCookie] = useState(false);
  const [resolvedBilibiliCookie, setResolvedBilibiliCookie] = useState('');
  const [uploadingCookie, setUploadingCookie] = useState(false);
  const cookieInputRef = React.useRef(null);

  // Status & Feedback
  const [testResult, setTestResult] = useState(null);
  const [geminiTestResult, setGeminiTestResult] = useState(null);
  const [saveMessage, setSaveMessage] = useState(null);

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/v1/settings');
      if (res.ok) {
        const data = await res.json();
        
        // Đọc trực tiếp từ file .env qua API máy chủ (Nguồn chuẩn duy nhất, không dùng localStorage đè bậy)
        setGeminiKey(data.gemini_api_key || '');
        setGeminiModel(data.gemini_model || 'gemini-3.1-flash-lite');
        setGroqKey(data.groq_api_key || '');
        setGroqModel(data.groq_model || 'whisper-large-v3');
        setAsrEngine(data.asr_engine || 'capcut');
        setWhisperSize(data.whisper_model_size || 'base');
        setOpenrouterKey(data.openrouter_api_key || '');
        setTranslationMaxChars(data.translation_max_chars || 50000);
        setTranslationBatchSize(data.translation_batch_size || 500);
        if (data.translation_temperature !== undefined && data.translation_temperature !== null) {
          setTranslationTemperature(data.translation_temperature);
        }
        setTtsThreads(data.tts_threads || 128);
        setTtsEngine(data.tts_engine || 'capcut');
        setCapcutCookie(data.capcut_cookie || '');
        setTiktokSessionId(data.tiktok_session_id || '410bfa37bdc185e1c6da82e1afb48409');
        setBilibiliCookieFile(data.bilibili_cookie_file || data.resolved_bilibili_cookie || '');
        setHasBilibiliCookie(!!data.has_bilibili_cookie);
        setResolvedBilibiliCookie(data.resolved_bilibili_cookie || '');
        if (data.gemini_web_model) setGeminiWebModel(data.gemini_web_model);
        if (data.gemini_web_thinking) setGeminiWebThinking(data.gemini_web_thinking);
        const currentMode = data.translation_provider === 'gemini_web' ? 'web' : 'api';
        setGeminiMode(currentMode);
      }

      // Kiểm tra trạng thái phiên Gemini Web
      try {
        const webRes = await fetch('/api/v1/translate/gemini-web/status');
        if (webRes.ok) {
          const webData = await webRes.json();
          setGeminiWebStatus(webData);
        }
      } catch (e) {
        console.error('Lỗi kiểm tra Gemini Web status:', e);
      }
    } catch (err) {
      console.error('Lỗi khi tải cài đặt:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCookieFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setUploadingCookie(true);
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/v1/settings/upload-cookie', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (res.ok) {
        setBilibiliCookieFile(data.cookie_file || 'cookies.txt');
        setHasBilibiliCookie(true);
        setResolvedBilibiliCookie(data.resolved_bilibili_cookie || '');
        setSaveMessage({ success: true, text: data.message || '🎉 Đã nạp file cookie thành công!' });
        setTimeout(() => setSaveMessage(null), 4000);
      } else {
        alert(data.detail || 'Lỗi khi nạp file cookie');
      }
    } catch (err) {
      alert('Lỗi mạng khi tải lên file cookie: ' + err.message);
    } finally {
      setUploadingCookie(false);
      if (cookieInputRef.current) cookieInputRef.current.value = '';
    }
  };

  const handleTestGemini = async () => {
    const rawKey = geminiKey.trim();
    if (!rawKey) {
      setGeminiTestResult({ success: false, message: 'Vui lòng nhập Google Gemini API Key trước khi kiểm tra!' });
      return;
    }
    setTestingGemini(true);
    setGeminiTestResult(null);
    try {
      const res = await fetch('/api/v1/settings/test-gemini', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: rawKey })
      });
      const data = await res.json();
      if (res.ok) {
        setGeminiTestResult({
          success: true,
          message: data.message || '🎉 Kết nối Google Gemini API thành công! Đã tự động lưu vĩnh viễn vào hệ thống.'
        });
        localStorage.setItem('saved_gemini_key', rawKey);
        fetchSettings();
      } else {
        setGeminiTestResult({ success: false, message: data.detail || 'Không thể kết nối Google Gemini API.' });
      }
    } catch (err) {
      setGeminiTestResult({ success: false, message: 'Lỗi mạng khi kết nối máy chủ kiểm tra API.' });
    } finally {
      setTestingGemini(false);
    }
  };

  const handleTestGeminiWeb = async () => {
    setTestingGeminiWeb(true);
    setGeminiWebTestResult(null);
    try {
      const res = await fetch('/api/v1/translate/gemini-web/status');
      const data = await res.json();
      if (data.logged_in) {
        setGeminiWebStatus(data);
        setGeminiWebTestResult({
          success: true,
          message: '🎉 Phiên Google Gemini Web đang hoạt động tốt! Sẵn sàng dịch thuật.'
        });
      } else {
        setGeminiWebTestResult({
          success: false,
          message: 'Chưa có phiên đăng nhập Google. Vui lòng bấm "Đăng Nhập / Đổi Nick" bên dưới.'
        });
      }
    } catch (err) {
      setGeminiWebTestResult({ success: false, message: 'Lỗi khi kiểm tra kết nối Gemini Web.' });
    } finally {
      setTestingGeminiWeb(false);
    }
  };

  const handleTriggerLogin = async () => {
    try {
      alert('Đang mở cửa sổ Chromium riêng biệt... Vui lòng đăng nhập Google vào gemini.google.com rồi cửa sổ sẽ tự đóng!');
      const res = await fetch('/api/v1/translate/gemini-web/login', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        setGeminiWebTestResult({ success: true, message: data.message });
        const webRes = await fetch('/api/v1/translate/gemini-web/status');
        const webData = await webRes.json();
        setGeminiWebStatus(webData);
      } else {
        setGeminiWebTestResult({ success: false, message: data.message });
      }
    } catch (e) {
      alert('Lỗi khởi chạy trình duyệt: ' + e.message);
    }
  };

  const handleTestGroq = async () => {
    setTestingGroq(true);
    setTestResult(null);
    try {
      const res = await fetch('/api/v1/settings/test-groq', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: groqKey.trim() || undefined })
      });
      const data = await res.json();
      if (res.ok) {
        setTestResult({ success: true, message: data.message || '🎉 Kết nối Groq thành công! Đã tự động lưu vĩnh viễn vào hệ thống.' });
        if (groqKey.trim()) localStorage.setItem('saved_groq_key', groqKey.trim());
        fetchSettings();
      } else {
        setTestResult({ success: false, message: data.detail || 'Không thể kết nối Groq API.' });
      }
    } catch (err) {
      setTestResult({ success: false, message: 'Lỗi mạng khi kết nối máy chủ.' });
    } finally {
      setTestingGroq(false);
    }
  };

  const handleSaveSettings = async (e) => {
    if (e) e.preventDefault();
    setSaving(true);
    setSaveMessage(null);
    try {
      const gK = geminiKey.trim();
      const grK = groqKey.trim();
      const orK = openrouterKey.trim();

      // Lưu key vào localStorage
      if (gK) localStorage.setItem('saved_gemini_key', gK);
      if (grK) localStorage.setItem('saved_groq_key', grK);
      if (orK) localStorage.setItem('saved_openrouter_key', orK);

      const payload = {
        gemini_api_key: gK,
        gemini_model: geminiModel,
        translation_provider: geminiMode === 'web' ? 'gemini_web' : 'gemini',
        gemini_web_model: geminiWebModel,
        gemini_web_thinking: geminiWebThinking,
        groq_api_key: grK,
        groq_model: groqModel,
        asr_engine: asrEngine,
        whisper_model_size: whisperSize,
        openrouter_api_key: orK,
        translation_max_chars: parseInt(translationMaxChars, 10) || 50000,
        translation_batch_size: parseInt(translationBatchSize, 10) || 500,
        translation_temperature: parseFloat(translationTemperature) || 0.85,
        tts_threads: parseInt(ttsThreads, 10) || 128,
        tts_engine: ttsEngine,
        capcut_cookie: capcutCookie.trim(),
        tiktok_session_id: tiktokSessionId.trim(),
        bilibili_cookie_file: bilibiliCookieFile.trim()
      };

      const res = await fetch('/api/v1/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok) {
        if (data.settings) {
          setHasBilibiliCookie(!!data.settings.has_bilibili_cookie);
          setResolvedBilibiliCookie(data.settings.resolved_bilibili_cookie || '');
          window.dispatchEvent(new CustomEvent('settings-updated', { detail: data.settings }));
        }
        setSaveMessage({ success: true, text: '🎉 Đã lưu toàn bộ cấu hình vào file .env và cập nhật tức thì toàn hệ thống!' });
        setTimeout(() => setSaveMessage(null), 4000);
      } else {
        setSaveMessage({ success: false, text: data.detail || 'Lỗi khi lưu cấu hình.' });
      }
    } catch (err) {
      setSaveMessage({ success: false, text: 'Lỗi mạng khi gửi yêu cầu.' });
    } finally {
      setSaving(false);
    }
  };

  const geminiModelsList = [
    {
      id: 'gemini-3.1-flash-lite',
      title: '⚡ Gemini 3.1 Flash Lite',
      badge: 'Khuyên Dùng - Siêu Tốc',
      desc: 'Tốc độ phản hồi nhanh nhất, tiết kiệm quota API, dịch mượt mà không độ trễ.'
    },
    {
      id: 'gemini-3.5-flash-lite',
      title: '🚀 Gemini 3.5 Flash Lite',
      badge: 'Cân Bằng Chuẩn',
      desc: 'Cân bằng giữa tốc độ cực nhanh và khả năng hiểu ngữ cảnh phim phức tạp.'
    },
    {
      id: 'gemini-flash-latest',
      title: '🌟 Gemini Flash Latest',
      badge: 'Bản Mới Nhất • Cực Thông Minh',
      desc: 'Tự động trỏ về bản Flash frontier mới nhất của Google, bắt trọn ngữ khí âm thanh và dịch chuẩn xác.'
    }
  ];

  const batchSentencesTiers = [
    { value: 150, label: '150 Câu / Lô', sub: 'Lô vừa (~5-7 phút phim)' },
    { value: 250, label: '250 Câu / Lô', sub: 'Lô chuẩn (~10-15 phút)' },
    { value: 300, label: '300 Câu / Lô', sub: 'Chuẩn AIRead (Khuyên dùng)' },
    { value: 400, label: '400 Câu / Lô', sub: 'Lô lớn (~20-25 phút)' },
    { value: 500, label: '500 Câu / Lô', sub: 'Siêu Lớn (Dịch 1-2 lần là xong)' }
  ];

  const ttsThreadsTiers = [
    { value: 16, label: '16 Luồng', tag: 'Cơ Bản', desc: 'Nhẹ máy, ổn định' },
    { value: 32, label: '32 Luồng', tag: 'Nhanh', desc: 'Tạo voiceover trong ~10s' },
    { value: 64, label: '64 Luồng', tag: 'Chuẩn Studio', desc: 'Khuyên dùng, siêu tốc độ' },
    { value: 96, label: '96 Luồng', tag: 'Cực Nhanh', desc: 'Tạo 100 câu chỉ trong ~4s' },
    { value: 128, label: '128 Luồng', tag: '🚀 Max Speed', desc: 'Tốc độ tối đa, xong tức thì' }
  ];

  return (
    <div style={{ padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '1480px', margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
      
      {/* Header Banner */}
      <div style={{
        background: 'linear-gradient(135deg, #dc2626 0%, #991b1b 60%, #7f1d1d 100%)',
        border: '1.5px solid #ef4444',
        padding: '22px 26px',
        borderRadius: '12px',
        boxShadow: '0 8px 24px rgba(220, 38, 38, 0.25)'
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
              <span style={{
                background: 'rgba(255, 255, 255, 0.20)',
                color: '#ffffff',
                border: '1px solid rgba(255, 255, 255, 0.35)',
                padding: '4px 10px',
                fontSize: '11px',
                fontWeight: 800,
                borderRadius: '5px',
                letterSpacing: '0.4px',
                backdropFilter: 'blur(4px)'
              }}>
                ⚙️ SYSTEM CONFIGURATION
              </span>
              <span style={{
                background: '#fef08a',
                color: '#854d0e',
                border: '1px solid #fde047',
                padding: '4px 10px',
                fontSize: '11px',
                fontWeight: 800,
                borderRadius: '5px',
                letterSpacing: '0.4px'
              }}>
                ⚡ 1-CLICK PRESETS
              </span>
            </div>
            <h1 style={{
              fontSize: '24px',
              fontWeight: 900,
              color: '#ffffff',
              marginBottom: '6px',
              textShadow: '0 2px 4px rgba(0, 0, 0, 0.35)',
              letterSpacing: '0.3px'
            }}>
              Cài Đặt Hệ Thống & AI Studio
            </h1>
            <p style={{
              color: '#fee2e2',
              fontSize: '13.5px',
              fontWeight: 500,
              maxWidth: '750px',
              margin: 0,
              lineHeight: 1.5,
              textShadow: '0 1px 2px rgba(0, 0, 0, 0.2)'
            }}>
              Chọn nhanh các mức cấu hình bằng nút bấm 1 chạm (không cần kéo thanh trượt). Quản lý API Key, chọn mô hình Gemini và luồng song song TikTok lên tới 128 luồng.
            </p>
          </div>

          <button 
            type="button"
            onClick={handleSaveSettings}
            disabled={saving}
            style={{
              padding: '12px 24px',
              fontSize: '14px',
              fontWeight: 900,
              background: '#ffffff',
              color: '#b91c1c',
              border: '2px solid #ffffff',
              borderRadius: '8px',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.25)',
              cursor: saving ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              transition: 'all 0.2s ease'
            }}
            title="Lưu toàn bộ cài đặt hệ thống"
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} color="#b91c1c" />}
            LƯU TẤT CẢ CÀI ĐẶT
          </button>
        </div>
      </div>

      {saveMessage && (
        <div style={{
          padding: '12px 18px',
          borderRadius: '10px',
          fontSize: '13px',
          fontWeight: 700,
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          background: saveMessage.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
          border: `1px solid ${saveMessage.success ? 'var(--emerald)' : 'var(--rose)'}`,
          color: saveMessage.success ? 'var(--emerald)' : 'var(--rose)',
          boxShadow: '0 4px 15px rgba(0,0,0,0.2)'
        }}>
          {saveMessage.success ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
          {saveMessage.text}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '60px', gap: '12px', color: 'var(--text-sub)' }}>
          <Loader2 size={28} className="animate-spin" /> Đang tải dữ liệu cấu hình hệ thống...
        </div>
      ) : (
        <form onSubmit={handleSaveSettings} style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          
          {/* ========================================================================= */}
          {/* SECTION 1: GOOGLE GEMINI AI TRANSLATION (3.1 / 3.5 MODELS + BATCH CHARS) */}
          {/* ========================================================================= */}
          <div className="glass-panel" style={{
            padding: '24px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(56, 189, 248, 0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '20px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(56, 189, 248, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Bot size={18} color="var(--cyan)" />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    1. Cấu Hình Dịch Thuật Google Gemini AI
                  </h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-sub)', margin: '2px 0 0 0' }}>
                    Chọn 1 trong 2 phương thức: Dùng Gemini Web (Tài Khoản Google) hoặc Dùng Gemini API Key
                  </p>
                </div>
              </div>

              {/* Mode Switcher Buttons */}
              <div style={{ display: 'flex', gap: '8px', background: 'rgba(0,0,0,0.3)', padding: '4px', borderRadius: '8px', border: '1px solid var(--border)' }}>
                <button
                  type="button"
                  onClick={() => handleModeSwitch('web')}
                  style={{
                    padding: '8px 16px',
                    borderRadius: '6px',
                    fontSize: '12px',
                    fontWeight: 800,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    background: geminiMode === 'web' ? 'var(--cyan)' : 'transparent',
                    color: geminiMode === 'web' ? '#070913' : 'var(--text-sub)',
                    border: 'none',
                    boxShadow: geminiMode === 'web' ? '0 0 15px rgba(0, 242, 254, 0.4)' : 'none',
                    transition: 'all 0.2s ease'
                  }}
                >
                  <Globe size={14} /> 🌐 Gehihi (Tài Khoản Google)
                </button>
                <button
                  type="button"
                  onClick={() => handleModeSwitch('api')}
                  style={{
                    padding: '8px 16px',
                    borderRadius: '6px',
                    fontSize: '12px',
                    fontWeight: 800,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    background: geminiMode === 'api' ? 'var(--cyan)' : 'transparent',
                    color: geminiMode === 'api' ? '#070913' : 'var(--text-sub)',
                    border: 'none',
                    boxShadow: geminiMode === 'api' ? '0 0 15px rgba(0, 242, 254, 0.4)' : 'none',
                    transition: 'all 0.2s ease'
                  }}
                >
                  <Zap size={14} /> ⚡ Gemini API Key (Studio)
                </button>
              </div>
            </div>

            {/* --- KHU VỰC 1: GEMINI WEB (TỰ ĐỘNG SÁNG KHI CHỌN WEB, TỐI MỜ KHI CHỌN API) --- */}
            <div style={{
              padding: '18px',
              borderRadius: '10px',
              border: geminiMode === 'web' ? '2px solid var(--cyan)' : '1px solid var(--border)',
              background: geminiMode === 'web' ? 'rgba(0, 242, 254, 0.05)' : 'rgba(255, 255, 255, 0.01)',
              opacity: geminiMode === 'web' ? 1 : 0.35,
              pointerEvents: geminiMode === 'web' ? 'auto' : 'none',
              boxShadow: geminiMode === 'web' ? '0 0 20px rgba(0, 242, 254, 0.15)' : 'none',
              transition: 'all 0.3s ease',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span className={`tag-badge ${geminiWebStatus.logged_in ? 'emerald' : 'rose'}`} style={{ fontSize: '11px', fontWeight: 800 }}>
                    {geminiWebStatus.logged_in ? '🟢 ĐÃ KẾT NỐI VỚI GOOGLE' : '🔴 CHƯA ĐĂNG NHẬP GOOGLE'}
                  </span>
                  <span style={{ fontSize: '12.5px', color: 'var(--text-main)', fontWeight: 600 }}>
                    {geminiWebStatus.logged_in 
                      ? 'Phiên đăng nhập đã lưu trong database/gemini_profile (Không tốn RAM, tự đóng ngầm sau 2s)'
                      : 'Chưa có phiên. Vui lòng bấm "Đăng Nhập / Đổi Nick" để mở Chromium đăng nhập 1 lần.'}
                  </span>
                </div>

                <div style={{ display: 'flex', gap: '8px' }}>
                  <button
                    type="button"
                    onClick={handleTestGeminiWeb}
                    disabled={testingGeminiWeb}
                    style={{
                      padding: '7px 14px',
                      fontSize: '12px',
                      fontWeight: 700,
                      borderRadius: '6px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      background: 'rgba(0, 242, 254, 0.15)',
                      border: '1px solid var(--cyan)',
                      color: 'var(--cyan)',
                      cursor: testingGeminiWeb ? 'not-allowed' : 'pointer'
                    }}
                  >
                    {testingGeminiWeb ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
                    {testingGeminiWeb ? 'Đang Kiểm Tra...' : '⚡ Kiểm Tra Kết Nối Gehihi'}
                  </button>

                  <button
                    type="button"
                    onClick={handleTriggerLogin}
                    style={{
                      padding: '7px 14px',
                      fontSize: '12px',
                      fontWeight: 700,
                      borderRadius: '6px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      background: 'rgba(255, 255, 255, 0.08)',
                      border: '1px solid var(--border)',
                      color: 'var(--text-main)',
                      cursor: 'pointer'
                    }}
                  >
                    <ExternalLink size={13} /> 🔄 Đăng Nhập / Đổi Nick Google
                  </button>
                </div>
              </div>

              {/* BỘ CHỌN MÔ HÌNH VÀ CẤP ĐỘ SUY LUẬN CHUẨN GOOGLE GEMINI */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(280px, 1.1fr) minmax(320px, 1.4fr)',
                gap: '16px',
                padding: '18px',
                borderRadius: '12px',
                background: '#ffffff',
                border: '1.5px solid #cbd5e1',
                boxShadow: '0 4px 16px rgba(15, 23, 42, 0.06)'
              }}>
                {/* Cột Trái: Mô Hình Cố Định (Chuẩn Thao Tác Người Dùng - Tự động Gemini 3) */}
                <div>
                  <label style={{ fontSize: '13px', fontWeight: 800, color: '#0f172a', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px' }}>
                    🤖 Mô Hình Trình Duyệt:
                  </label>
                  <div style={{
                    padding: '14px 16px',
                    borderRadius: '8px',
                    border: '2px solid #0284c7',
                    background: '#f0f9ff',
                    color: '#0f172a',
                    boxShadow: '0 0 12px rgba(2, 132, 199, 0.2)'
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ fontWeight: 800, fontSize: '14px', color: '#0284c7' }}>
                        🌐 Tự động (Các mô hình Gemini 3)
                      </div>
                      <span style={{
                        padding: '3px 8px',
                        borderRadius: '4px',
                        fontSize: '10.5px',
                        fontWeight: 800,
                        background: '#0284c7',
                        color: '#ffffff'
                      }}>
                        MẶC ĐỊNH
                      </span>
                    </div>
                    <div style={{ fontSize: '12px', color: '#0369a1', marginTop: '6px', fontWeight: 500, lineHeight: '1.4' }}>
                      Google tự động tối ưu hóa tài nguyên phần cứng & phản hồi theo thời gian thực (giống hệt thao tác tay người dùng).
                    </div>
                  </div>
                </div>

                {/* Cột Phải: 3 Cấp Độ Tư Duy / Suy Luận */}
                <div>
                  <label style={{ fontSize: '13px', fontWeight: 800, color: '#0f172a', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px' }}>
                    🧠 Cấp Độ Tư Duy (Thinking Level):
                  </label>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {[
                      { key: 'low', label: '⚡ Thấp (Nhanh Chóng & Hiệu Quả)', sub: 'Tốc độ phản hồi cực nhanh ~5s-7s, lý tưởng cho dịch truyện', border: '#16a34a', bg: '#f0fdf4', text: '#15803d' },
                      { key: 'medium', label: '⚖️ Trung Bình (Độ Sâu Cân Bằng)', sub: 'Cân đối thời gian ~10s-15s, ngữ cảnh trôi chảy tự nhiên', border: '#0284c7', bg: '#f0f9ff', text: '#0369a1' },
                      { key: 'high', label: '🔬 Cao (Cực Kỳ Kỹ Lưỡng)', sub: 'Suy luận ngữ nghĩa sâu ~15s-20s, xử lý từ Hán Việt cổ trang phức tạp', border: '#d97706', bg: '#fffbeb', text: '#b45309' }
                    ].map((item) => {
                      const isActive = geminiWebThinking === item.key;
                      return (
                        <button
                          key={item.key}
                          type="button"
                          onClick={() => setGeminiWebThinking(item.key)}
                          style={{
                            padding: '10px 14px',
                            borderRadius: '8px',
                            border: isActive ? `2px solid ${item.border}` : '1px solid #cbd5e1',
                            background: isActive ? item.bg : '#f8fafc',
                            color: '#0f172a',
                            cursor: 'pointer',
                            textAlign: 'left',
                            boxShadow: isActive ? `0 0 10px ${item.border}30` : 'none',
                            transition: 'all 0.2s ease',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center'
                          }}
                        >
                          <div>
                            <div style={{ fontWeight: 800, fontSize: '13px', color: isActive ? item.text : '#0f172a' }}>
                              {item.label}
                            </div>
                            <div style={{ fontSize: '11px', color: isActive ? item.text : '#64748b', marginTop: '2px', fontWeight: 500 }}>
                              {item.sub}
                            </div>
                          </div>
                          {isActive && (
                            <span style={{ fontSize: '12px', fontWeight: 800, color: item.text }}>✓ Đang Chọn</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>

              {geminiWebTestResult && (
                <div style={{
                  padding: '10px 14px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: geminiWebTestResult.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
                  border: `1px solid ${geminiWebTestResult.success ? 'var(--emerald)' : 'var(--rose)'}`,
                  color: geminiWebTestResult.success ? 'var(--emerald)' : 'var(--rose)'
                }}>
                  {geminiWebTestResult.success ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                  {geminiWebTestResult.message}
                </div>
              )}
            </div>

            {/* --- KHU VỰC 2: GEMINI API KEY (SÁNG KHI CHỌN API, TỐI MỜ KHI CHỌN WEB) --- */}
            <div style={{
              padding: '18px',
              borderRadius: '10px',
              border: geminiMode === 'api' ? '2px solid var(--cyan)' : '1px solid var(--border)',
              background: geminiMode === 'api' ? 'rgba(0, 242, 254, 0.05)' : 'rgba(255, 255, 255, 0.01)',
              opacity: geminiMode === 'api' ? 1 : 0.35,
              pointerEvents: geminiMode === 'api' ? 'auto' : 'none',
              boxShadow: geminiMode === 'api' ? '0 0 20px rgba(0, 242, 254, 0.15)' : 'none',
              transition: 'all 0.3s ease',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px'
            }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '8px' }}>
                  <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-main)', margin: 0 }}>
                    Google Gemini API Key (Bắt đầu bằng AIzaSy...):
                  </label>
                  <button
                    type="button"
                    onClick={handleTestGemini}
                    disabled={testingGemini || !geminiKey.trim()}
                  style={{
                    padding: '6px 14px',
                    fontSize: '12px',
                    fontWeight: 700,
                    borderRadius: '6px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    background: 'linear-gradient(135deg, rgba(0, 242, 254, 0.25) 0%, rgba(79, 172, 254, 0.25) 100%)',
                    border: '1px solid var(--cyan)',
                    color: 'var(--cyan)',
                    cursor: (testingGemini || !geminiKey.trim()) ? 'not-allowed' : 'pointer',
                    boxShadow: '0 0 12px rgba(0, 242, 254, 0.25)',
                    transition: 'all 0.2s ease'
                  }}
                >
                  {testingGemini ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                  {testingGemini ? 'Đang Kiểm Tra Key...' : '⚡ Kiểm Tra Key Gemini'}
                </button>
              </div>

              <div style={{ position: 'relative' }}>
                <input
                  type={showGeminiKey ? 'text' : 'password'}
                  placeholder="Dán Google Gemini API Key (AIzaSy...) vào đây..."
                  value={geminiKey}
                  onChange={(e) => setGeminiKey(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '12px 42px 12px 14px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-main)',
                    fontSize: '13px',
                    fontFamily: 'var(--font-mono)'
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowGeminiKey(!showGeminiKey)}
                  style={{
                    position: 'absolute',
                    right: '12px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-dim)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                  title={showGeminiKey ? 'Ẩn API Key' : 'Hiện API Key'}
                >
                  {showGeminiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              {/* Warning if user pasted Groq key into Gemini */}
              {geminiKey.trim().startsWith('gsk_') && (
                <div style={{
                  marginTop: '8px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  background: 'rgba(244, 63, 94, 0.15)',
                  border: '1px solid var(--rose)',
                  color: 'var(--rose)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}>
                  <AlertCircle size={14} />
                  <span><strong>Nhầm loại Key:</strong> Bạn đang nhập Groq API Key (<code>gsk_...</code>). Google Gemini API Key phải bắt đầu bằng <code>AIzaSy...</code>.</span>
                </div>
              )}

              {/* Gemini Test Feedback Result */}
              {geminiTestResult && (
                <div style={{
                  marginTop: '10px',
                  padding: '10px 14px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: geminiTestResult.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
                  border: `1px solid ${geminiTestResult.success ? 'var(--emerald)' : 'var(--rose)'}`,
                  color: geminiTestResult.success ? 'var(--emerald)' : 'var(--rose)'
                }}>
                  {geminiTestResult.success ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                  {geminiTestResult.message}
                </div>
              )}
            </div>

            {/* 1A. Mức Chọn Mô Hình Gemini (Card Buttons Bấm Chọn 1 Chạm) */}
            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 700, color: 'var(--text-main)', marginBottom: '10px' }}>
                Chọn Mô Hình Dịch Thuật Gemini (Bấm chọn mức):
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px' }}>
                {geminiModelsList.map((m) => {
                  const isSelected = geminiModel === m.id;
                  return (
                    <div
                      key={m.id}
                      onClick={() => setGeminiModel(m.id)}
                      style={{
                        padding: '16px',
                        borderRadius: '10px',
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(0, 242, 254, 0.12)' : 'rgba(255, 255, 255, 0.02)',
                        border: isSelected ? '2px solid var(--cyan)' : '1px solid var(--border)',
                        boxShadow: isSelected ? '0 0 15px rgba(0, 242, 254, 0.25)' : 'none',
                        transition: 'all 0.2s ease',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <strong style={{ fontSize: '14px', color: isSelected ? 'var(--cyan)' : 'var(--text-main)' }}>
                          {m.title}
                        </strong>
                        <span className={`tag-badge ${isSelected ? 'cyan' : 'emerald'}`} style={{ fontSize: '10px' }}>
                          {m.badge}
                        </span>
                      </div>
                      <p style={{ fontSize: '11.5px', color: 'var(--text-sub)', margin: 0, lineHeight: 1.5 }}>
                        {m.desc}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

            {/* 1B. Mức Chọn Số Câu Một Lô (Batch Sentences: 150 - 500 câu chuẩn AIRead) */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-main)', margin: 0 }}>
                  Chọn Số Câu Một Lô Dịch (Lô Lớn 150 – 500 Câu Chuẩn AIRead):
                </label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Tùy chỉnh:</span>
                  <input
                    type="number"
                    min="50"
                    max="1000"
                    step="50"
                    value={translationBatchSize}
                    onChange={(e) => setTranslationBatchSize(parseInt(e.target.value, 10) || 300)}
                    style={{
                      width: '75px',
                      padding: '4px 8px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      borderRadius: '4px',
                      color: 'var(--cyan)',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)',
                      fontWeight: 800,
                      textAlign: 'center'
                    }}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>câu</span>
                </div>
              </div>

              {/* Preset Buttons Grid for Sentences */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: '10px' }}>
                {batchSentencesTiers.map((t) => {
                  const isSelected = translationBatchSize === t.value;
                  return (
                    <div
                      key={t.value}
                      onClick={() => setTranslationBatchSize(t.value)}
                      style={{
                        padding: '12px 14px',
                        borderRadius: '8px',
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(0, 242, 254, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                        border: isSelected ? '2px solid var(--cyan)' : '1px solid var(--border)',
                        transition: 'all 0.2s ease',
                        textAlign: 'center'
                      }}
                    >
                      <strong style={{ fontSize: '13px', color: isSelected ? 'var(--cyan)' : 'var(--text-main)', display: 'block', marginBottom: '2px' }}>
                        {t.label}
                      </strong>
                      <span style={{ fontSize: '11px', color: isSelected ? 'var(--text-main)' : 'var(--text-dim)' }}>
                        {t.sub}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 1C. Mức Sáng Tạo & Thông Minh (Temperature: 0.1 – 1.0) */}
            <div style={{
              padding: '16px 18px',
              borderRadius: '10px',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid var(--border)',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Sparkles size={16} color="var(--cyan)" />
                  <label style={{ fontSize: '12.5px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    Độ Sáng Tạo & Độ Thông Minh (Temperature):
                  </label>
                  <span className="tag-badge cyan" style={{ fontSize: '11px', fontWeight: 800, fontFamily: 'var(--font-mono)' }}>
                    {translationTemperature}
                  </span>
                </div>
                <span style={{ fontSize: '12px', color: 'var(--text-sub)', fontWeight: 600 }}>
                  {translationTemperature <= 0.3
                    ? '🎯 Rập khuôn chính xác, bám sát từng chữ'
                    : translationTemperature <= 0.7
                    ? '✨ Lồng tiếng tự nhiên, biểu cảm mượt mà (Khuyên Dùng)'
                    : '🚀 Siêu sáng tạo, ngôn từ phong phú bay bổng'}
                </span>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                <span style={{ fontSize: '11px', color: 'var(--text-dim)', fontWeight: 700 }}>0.1 (Chính xác)</span>
                <input
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={translationTemperature}
                  onChange={(e) => setTranslationTemperature(parseFloat(e.target.value))}
                  style={{
                    flex: 1,
                    accentColor: 'var(--cyan)',
                    cursor: 'pointer',
                    height: '6px'
                  }}
                />
                <span style={{ fontSize: '11px', color: 'var(--text-dim)', fontWeight: 700 }}>1.0 (Bay bổng)</span>
              </div>

              {/* Nút Preset 1-chạm */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '8px' }}>
                {[
                  { val: 0.2, label: '0.2 - Chuẩn Xác', desc: 'Bám sát nghĩa đen' },
                  { val: 0.5, label: '0.5 - Cân Bằng', desc: 'Trôi chảy, mạch lạc' },
                  { val: 0.65, label: '0.65 - Chuẩn Lồng Tiếng', desc: 'Khuyên dùng (Văn nói mượt mà)', isRec: true },
                  { val: 0.85, label: '0.85 - Siêu Hấp Dẫn', desc: 'Giàu cảm xúc, kịch tính' }
                ].map((preset) => {
                  const isSelected = Math.abs(translationTemperature - preset.val) < 0.02;
                  return (
                    <button
                      key={preset.val}
                      type="button"
                      onClick={() => setTranslationTemperature(preset.val)}
                      style={{
                        padding: '8px 12px',
                        borderRadius: '6px',
                        border: isSelected ? '1.5px solid var(--cyan)' : '1px solid var(--border)',
                        background: isSelected ? 'rgba(0, 242, 254, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                        color: isSelected ? 'var(--cyan)' : 'var(--text-main)',
                        cursor: 'pointer',
                        textAlign: 'left',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '2px',
                        transition: 'all 0.15s ease'
                      }}
                    >
                      <div style={{ fontSize: '12px', fontWeight: 800, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span>{preset.label}</span>
                        {preset.isRec && <span style={{ fontSize: '10px', color: '#f59e0b', fontWeight: 900 }}>★ KHUYÊN DÙNG</span>}
                      </div>
                      <div style={{ fontSize: '10.5px', color: isSelected ? 'var(--text-main)' : 'var(--text-dim)' }}>
                        {preset.desc}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

          </div>

          {/* ========================================================================= */}
          {/* SECTION 2: TIKTOK TTS MULTI-THREADING (PRESET TIERS UP TO 128 WORKERS)   */}
          {/* ========================================================================= */}
          <div className="glass-panel" style={{
            padding: '24px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(245, 158, 11, 0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '18px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(245, 158, 11, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Volume2 size={18} color="var(--amber)" />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    2. Cấu Hình Động Cơ Giọng Đọc AI & Đa Luồng (CapCut Cloud vs TikTok)
                  </h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-sub)', margin: '2px 0 0 0' }}>
                    Chọn nền tảng tạo giọng đọc lồng tiếng và mức độ xử lý song song lên tới 128 luồng
                  </p>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-sub)' }}>Đang chọn:</span>
                <span className={`tag-badge ${ttsEngine === 'capcut' ? 'cyan' : 'purple'}`} style={{ fontSize: '12px', padding: '4px 12px', fontWeight: 800 }}>
                  {ttsEngine === 'capcut' ? '⚡ CAPCUT CLOUD TTS' : '🎵 TIKTOK TTS'}
                </span>
                <span className="tag-badge amber" style={{ fontSize: '12px', padding: '4px 12px', fontWeight: 800, fontFamily: 'var(--font-mono)' }}>
                  ⚡ {ttsThreads} LUỒNG
                </span>
              </div>
            </div>

            {/* TTS Engine Selection: CapCut Cloud TTS vs TikTok TTS */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '14px' }}>
              {/* Option 1: CapCut Cloud TTS */}
              <div
                onClick={() => setTtsEngine('capcut')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: ttsEngine === 'capcut' ? 'rgba(0, 242, 254, 0.14)' : 'rgba(255, 255, 255, 0.02)',
                  border: ttsEngine === 'capcut' ? '2px solid var(--cyan)' : '1px solid var(--border)',
                  boxShadow: ttsEngine === 'capcut' ? '0 0 20px rgba(0, 242, 254, 0.28)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  transition: 'all 0.2s ease'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: ttsEngine === 'capcut' ? 'var(--cyan)' : 'var(--text-main)', fontSize: '15px' }}>
                    ⚡ CapCut Cloud TTS (ByteDance SAMI)
                  </strong>
                  <span className="tag-badge cyan" style={{ fontSize: '9.5px', fontWeight: 800 }}>
                    Khuyên Dùng • 280 Ký Tự
                  </span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', lineHeight: 1.45 }}>
                  Tự động xoay Device ID ngẫu nhiên để <strong>lách giới hạn Rate Limit khi chạy 128 luồng song song</strong>. Đọc câu dài 250 - 300 ký tự không bị cụt câu, tải trực tiếp MP3 từ ByteDance CDN.
                </div>
              </div>

              {/* Option 2: TikTok TTS API */}
              <div
                onClick={() => setTtsEngine('tiktok')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: ttsEngine === 'tiktok' ? 'rgba(168, 85, 247, 0.16)' : 'rgba(255, 255, 255, 0.02)',
                  border: ttsEngine === 'tiktok' ? '2px solid var(--purple)' : '1px solid var(--border)',
                  boxShadow: ttsEngine === 'tiktok' ? '0 0 18px rgba(168, 85, 247, 0.25)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  transition: 'all 0.2s ease'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: ttsEngine === 'tiktok' ? 'var(--purple-light)' : 'var(--text-main)', fontSize: '15px' }}>
                    🎵 TikTok TTS API (Nguyên Bản)
                  </strong>
                  <span className="tag-badge purple" style={{ fontSize: '9.5px', fontWeight: 800 }}>
                    Cần Session Cookie
                  </span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', lineHeight: 1.45 }}>
                  Gọi trực tiếp endpoint TikTok âm thanh gốc qua Cookie <code>sessionid</code>. Giới hạn khoảng 140 ký tự mỗi chunk (câu dài sẽ tự tách thành nhiều phần nhỏ).
                </div>
              </div>
            </div>

            {/* Chi tiết / Cấu hình Cookie tương ứng từng Engine */}
            {ttsEngine === 'capcut' ? (
              <div style={{
                padding: '14px 16px',
                borderRadius: '8px',
                background: 'rgba(0, 242, 254, 0.06)',
                border: '1px solid rgba(0, 242, 254, 0.22)',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Sparkles size={16} color="var(--cyan)" />
                    <span style={{ fontSize: '12.5px', color: 'var(--text-main)' }}>
                      Đang kích hoạt <strong>CapCut Cloud TTS Engine</strong>: Hỗ trợ trọn vẹn 9 giọng đọc tiếng Việt (Cô Gái Hoạt Ngôn, Nhỏ Ngọt Ngào, Mai, Hương, Nam Hào Sảng...). Tự động ký chữ ký số thiết bị.
                    </span>
                  </div>
                  <span className="tag-badge cyan" style={{ fontSize: '10px', fontWeight: 800 }}>
                    AUTO DEVICE ROTATION
                  </span>
                </div>
                <div>
                  <label style={{ fontSize: '11.5px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                    CapCut Cookie / Token (Tùy chọn nâng cao - Mặc định hệ thống tự động tạo Sign không cần cookie):
                  </label>
                  <input
                    type="text"
                    placeholder="Để trống nếu không cần, hoặc dán cookie CapCut nếu có..."
                    value={capcutCookie}
                    onChange={(e) => setCapcutCookie(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--text-main)',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)'
                    }}
                  />
                </div>
              </div>
            ) : (
              <div style={{
                padding: '14px 16px',
                borderRadius: '8px',
                background: 'rgba(168, 85, 247, 0.08)',
                border: '1px solid rgba(168, 85, 247, 0.25)',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Volume2 size={16} color="var(--purple-light)" />
                  <span style={{ fontSize: '12.5px', color: 'var(--text-main)' }}>
                    Đang chọn <strong>TikTok TTS Legacy Engine</strong>: Yêu cầu Cookie SessionID còn hạn sử dụng để gọi API.
                  </span>
                </div>
                <div>
                  <label style={{ fontSize: '11.5px', color: 'var(--text-sub)', display: 'block', marginBottom: '4px' }}>
                    TikTok Cookie Session ID (sessionid):
                  </label>
                  <input
                    type="text"
                    placeholder="Nhập sessionid TikTok (Mặc định: 410bfa37bdc185e1c6da82e1afb48409)..."
                    value={tiktokSessionId}
                    onChange={(e) => setTiktokSessionId(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--purple-light)',
                      fontSize: '12px',
                      fontFamily: 'var(--font-mono)'
                    }}
                  />
                </div>
              </div>
            )}

            {/* Mức Luồng Bấm Chọn (Clickable Tier Cards - Không Dùng Thanh Kéo) */}
            <div>
              <div style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--text-main)', marginBottom: '8px' }}>
                Mức độ phân luồng song song (Workers Concurrency):
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
                {ttsThreadsTiers.map((t) => {
                  const isSelected = ttsThreads === t.value;
                  return (
                    <div
                      key={t.value}
                      onClick={() => setTtsThreads(t.value)}
                      style={{
                        padding: '14px 12px',
                        borderRadius: '10px',
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(245, 158, 11, 0.18)' : 'rgba(255, 255, 255, 0.02)',
                        border: isSelected ? '2px solid var(--amber)' : '1px solid var(--border)',
                        boxShadow: isSelected ? '0 0 18px rgba(245, 158, 11, 0.3)' : 'none',
                        transition: 'all 0.2s ease',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '4px',
                        textAlign: 'center'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '6px' }}>
                        <strong style={{ fontSize: '15px', color: isSelected ? 'var(--amber)' : 'var(--text-main)' }}>
                          {t.label}
                        </strong>
                      </div>
                      <span className={`tag-badge ${isSelected ? 'amber' : 'emerald'}`} style={{ fontSize: '10px', margin: '2px auto' }}>
                        {t.tag}
                      </span>
                      <span style={{ fontSize: '11px', color: isSelected ? 'var(--text-main)' : 'var(--text-dim)', marginTop: '2px' }}>
                        {t.desc}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(0,0,0,0.2)', padding: '10px 14px', borderRadius: '6px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-dim)' }}>
                * Với mức 64 - 128 luồng, toàn bộ lời thoại video 10-20 phút được tạo giọng đọc trong <strong>~3 đến 5 giây</strong>.
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Nhập số khác:</span>
                <input
                  type="number"
                  min="1"
                  max="128"
                  value={ttsThreads}
                  onChange={(e) => setTtsThreads(Math.max(1, Math.min(128, parseInt(e.target.value, 10) || 64)))}
                  style={{
                    width: '60px',
                    padding: '3px 6px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    color: 'var(--amber)',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 800,
                    textAlign: 'center'
                  }}
                />
              </div>
            </div>
          </div>

          {/* ========================================================================= */}
          {/* SECTION 3: SPEECH-TO-TEXT ASR (GROQ WHISPER CLOUD / FASTER-WHISPER)      */}
          {/* ========================================================================= */}
          <div className="glass-panel" style={{
            padding: '24px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(168, 85, 247, 0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '18px'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'rgba(168, 85, 247, 0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <CloudLightning size={18} color="var(--purple)" />
                </div>
                <div>
                  <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                    3. Cấu Hình AI Bóc Tách Lời Thoại (Speech-to-Text ASR)
                  </h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-sub)', margin: '2px 0 0 0' }}>
                    Chuyển giọng nói tiếng Trung thành phụ đề và timecode từng mili-giây
                  </p>
                </div>
              </div>

              <a
                href="https://console.groq.com/keys"
                target="_blank"
                rel="noreferrer"
                style={{
                  fontSize: '12px',
                  color: 'var(--purple-light)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  textDecoration: 'none',
                  fontWeight: 700,
                  background: 'rgba(168, 85, 247, 0.1)',
                  padding: '5px 10px',
                  borderRadius: '6px',
                  border: '1px solid rgba(168, 85, 247, 0.25)'
                }}
              >
                Lấy Groq API Key <ExternalLink size={12} />
              </a>
            </div>

            {/* Groq API Key Input */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-main)' }}>
                  Groq Cloud API Key (Hiển thị trực tiếp):
                </label>
                <button
                  type="button"
                  onClick={handleTestGroq}
                  disabled={testingGroq}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--purple-light)',
                    fontSize: '11.5px',
                    fontWeight: 700,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}
                >
                  {testingGroq ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                  Kiểm tra kết nối Groq API
                </button>
              </div>

              <div style={{ position: 'relative' }}>
                <input
                  type={showGroqKey ? 'text' : 'password'}
                  placeholder="gsk_..."
                  value={groqKey}
                  onChange={(e) => setGroqKey(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '12px 42px 12px 14px',
                    background: 'var(--bg-input)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-main)',
                    fontSize: '13px',
                    fontFamily: 'var(--font-mono)'
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowGroqKey(!showGroqKey)}
                  style={{
                    position: 'absolute',
                    right: '12px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-dim)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                  title={showGroqKey ? 'Ẩn API Key' : 'Hiện API Key'}
                >
                  {showGroqKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>

              {testResult && (
                <div style={{
                  marginTop: '10px',
                  padding: '8px 12px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: testResult.success ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
                  border: `1px solid ${testResult.success ? 'var(--emerald)' : 'var(--rose)'}`,
                  color: testResult.success ? 'var(--emerald)' : 'var(--rose)'
                }}>
                  {testResult.success ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                  {testResult.message}
                </div>
              )}
            </div>

            {/* Engine Selection: CapCut vs Groq (Card Buttons) */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '14px' }}>
              {/* Option 1: CapCut Cloud STT (Khuyến nghị số 1) */}
              <div
                onClick={() => setAsrEngine('capcut')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: asrEngine === 'capcut' ? 'rgba(0, 242, 254, 0.16)' : 'rgba(255, 255, 255, 0.02)',
                  border: asrEngine === 'capcut' ? '2px solid var(--cyan)' : '1px solid var(--border)',
                  boxShadow: asrEngine === 'capcut' ? '0 0 18px rgba(0, 242, 254, 0.3)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px',
                  position: 'relative'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: asrEngine === 'capcut' ? 'var(--cyan)' : 'var(--text-main)', fontSize: '14.5px' }}>
                    ⚡ CapCut Cloud STT
                  </strong>
                  <span className="tag-badge cyan" style={{ fontSize: '9px', fontWeight: 800 }}>Khuyên Dùng • 0% CPU</span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', marginTop: '2px', lineHeight: 1.4 }}>
                  Siêu bóc timecode chuẩn từng mili-giây mở/ngậm miệng nhân vật. Không cần API Key, xử lý 10 phút chỉ trong 5-10 giây.
                </div>
              </div>

              {/* Option 2: Groq Whisper Cloud */}
              <div
                onClick={() => setAsrEngine('groq')}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  background: asrEngine === 'groq' ? 'rgba(168, 85, 247, 0.18)' : 'rgba(255, 255, 255, 0.02)',
                  border: asrEngine === 'groq' ? '2px solid var(--purple)' : '1px solid var(--border)',
                  boxShadow: asrEngine === 'groq' ? '0 0 15px rgba(168, 85, 247, 0.25)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '4px'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: asrEngine === 'groq' ? 'var(--purple-light)' : 'var(--text-main)', fontSize: '14px' }}>
                    🚀 Groq Whisper Large-v3
                  </strong>
                  <span className="tag-badge emerald" style={{ fontSize: '9px' }}>Cần API Key</span>
                </div>
                <div style={{ fontSize: '11.5px', color: 'var(--text-sub)', marginTop: '2px', lineHeight: 1.4 }}>
                  Bóc tách video qua Groq Cloud (cần nhập Groq API Key ở trên).
                </div>
              </div>
            </div>

            {/* Thông báo chi tiết khi chọn CapCut STT */}
            {asrEngine === 'capcut' && (
              <div style={{
                padding: '12px 16px',
                borderRadius: '8px',
                background: 'rgba(0, 242, 254, 0.08)',
                border: '1px solid rgba(0, 242, 254, 0.25)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: '10px'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Sparkles size={16} color="var(--cyan)" />
                  <span style={{ fontSize: '12.5px', color: 'var(--text-main)' }}>
                    Đang kích hoạt <strong>CapCut Cloud STT</strong>: Bóc tách lời thoại và tính toán khung mở/ngậm miệng chuẩn xác từng mili-giây qua máy chủ ByteDance Cloud. Miễn phí 100%, không tốn CPU.
                  </span>
                </div>
                <span className="tag-badge cyan" style={{ fontSize: '11px', fontWeight: 800 }}>
                  CHUẨN KHẨU HÌNH LỒNG TIẾNG
                </span>
              </div>
            )}
          </div>

          {/* Card: Bộ Tải Video & Cookie Bilibili */}
          <div className="glass-panel" style={{ padding: '24px', borderRadius: 'var(--radius-lg)', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <Rocket size={22} color="var(--cyan)" />
                <h3 style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
                  Bộ Tải Video & Cookie Bilibili (Direct IP • 16 Luồng • Không Proxy)
                </h3>
              </div>
              <span className={`tag-badge ${hasBilibiliCookie ? 'emerald' : 'cyan'}`} style={{ fontSize: '11px', fontWeight: 800 }}>
                {hasBilibiliCookie ? '✓ ĐÃ PHÁT HIỆN COOKIE HỢP LỆ' : 'CHẾ ĐỘ CÔNG KHAI (KHÔNG CẦN TÀI KHOẢN)'}
              </span>
            </div>

            <div style={{ fontSize: '12.5px', color: 'var(--text-sub)', lineHeight: 1.6 }}>
              Hệ thống tải video siêu tốc qua <strong>Aria2c 16 kết nối song song</strong> và CDN Bilibili tối ưu, <strong>tải trực tiếp không dùng proxy</strong>. Nếu bạn có tài khoản VIP/thành viên Bilibili, hãy dán đường dẫn file <code>cookies.txt</code> để mở khóa độ phân giải 1080p 60fps / 4K.
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <label style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-main)' }}>
                Đường Dẫn File Cookie Bilibili (cookies.txt):
              </label>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                  type="text"
                  placeholder="Nhập hoặc dán đường dẫn file cookies.txt..."
                  value={bilibiliCookieFile}
                  onChange={(e) => setBilibiliCookieFile(e.target.value)}
                  style={{
                    flex: '1 1 280px',
                    padding: '10px 14px',
                    borderRadius: '8px',
                    border: '1px solid var(--border)',
                    background: 'rgba(255, 255, 255, 0.04)',
                    color: 'var(--text-main)',
                    fontSize: '13px',
                    fontFamily: 'var(--font-mono)'
                  }}
                />

                {/* File input ẩn */}
                <input
                  type="file"
                  ref={cookieInputRef}
                  onChange={handleCookieFileUpload}
                  accept=".txt,.cookie"
                  style={{ display: 'none' }}
                />

                <button
                  type="button"
                  disabled={uploadingCookie}
                  onClick={() => cookieInputRef.current && cookieInputRef.current.click()}
                  className="btn btn-primary btn-sm"
                  style={{
                    whiteSpace: 'nowrap',
                    padding: '10px 14px',
                    fontSize: '12px',
                    fontWeight: 800,
                    background: 'var(--grad-cyan)',
                    color: '#070913',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px'
                  }}
                  title="Chọn file cookies.txt trực tiếp từ máy tính để nạp vào hệ thống"
                >
                  {uploadingCookie ? <Loader2 size={13} className="animate-spin" /> : <FolderDown size={14} />}
                  📁 Nạp File Cookie (.txt)
                </button>

                <button
                  type="button"
                  onClick={async () => {
                    try {
                      const text = await navigator.clipboard.readText();
                      if (text) setBilibiliCookieFile(text.trim());
                    } catch (e) {
                      console.warn(e);
                    }
                  }}
                  className="btn btn-secondary btn-sm"
                  style={{ whiteSpace: 'nowrap', padding: '10px 14px', fontSize: '12px' }}
                >
                  📋 Dán Đường Dẫn
                </button>
              </div>

              {resolvedBilibiliCookie ? (
                <div style={{ fontSize: '12px', color: 'var(--emerald)', display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
                  <CheckCircle2 size={14} />
                  <span>Đang sử dụng cookie từ: <code>{resolvedBilibiliCookie}</code></span>
                </div>
              ) : (
                <div style={{ fontSize: '12px', color: 'var(--text-dim)', marginTop: '2px' }}>
                  💡 Khi clone dự án sang máy tính khác chưa có file cookie, hệ thống vẫn tự động tải video ở chế độ công khai bình thường mà không phát sinh lỗi.
                </div>
              )}
            </div>
          </div>

          {/* Bottom Save Bar */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '10px' }}>
            <button
              type="submit"
              disabled={saving}
              className="btn btn-primary"
              style={{
                padding: '14px 36px',
                fontSize: '15px',
                fontWeight: 800,
                background: 'var(--grad-cyan)',
                color: '#070913',
                boxShadow: '0 0 25px rgba(0, 242, 254, 0.4)',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}
            >
              {saving ? <Loader2 size={18} className="animate-spin" /> : <Save size={18} />}
              LƯU TOÀN BỘ CÀI ĐẶT HỆ THỐNG
            </button>
          </div>

        </form>
      )}

      {/* Storage Architecture Overview */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <FolderDown size={18} color="var(--cyan)" />
            <h4 style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
              Cấu Trúc Thư Mục Lưu Trữ
            </h4>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-sub)', lineHeight: 1.6 }}>
            • <code>input/videos/</code>: Video MP4 gốc tải về.<br />
            • <code>input/audio_raw/</code>: Audio 16kHz WAV phục vụ AI ASR.<br />
            • <code>output/transcripts/</code>: File <code>.srt</code>, <code>.ass</code> Karaoke.<br />
            • <code>output/voiceover/</code>: Audio TikTok TTS lồng tiếng.<br />
            • <code>output/final_videos/</code>: Video hoàn chỉnh chuẩn FastStart.
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '20px', borderRadius: 'var(--radius-md)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <ShieldCheck size={18} color="var(--emerald)" />
            <h4 style={{ fontSize: '14px', fontWeight: 800, color: 'var(--text-main)', margin: 0 }}>
              Tối Ưu Hóa & Bộ Đệm RAM
            </h4>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-sub)', lineHeight: 1.6 }}>
            • <strong>Stream Copy 0.5s:</strong> Ghép video và âm thanh tức thì không encode lại.<br />
            • <strong>HTTP Range 206:</strong> Xem video trên web không tràn RAM hay giật lag.<br />
            • <strong>CUDA + Intel GPU:</strong> Phối hợp 2 GPU xử lý bóc tách và xuất video.
          </div>
        </div>
      </div>

    </div>
  );
}
