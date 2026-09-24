import re
from typing import List, Dict, Tuple, Set

# Danh sách từ ghép tiếng Hán an toàn (Safe Compounds / Whitelist)
# Tuyệt đối KHÔNG ĐƯỢC bóc tách hoặc mask bất kỳ chuỗi con nào thuộc các từ này!
# Bao phủ TOÀN BỘ ký tự đa nghĩa (polysemous characters) trong tiếng Trung
SAFE_COMPOUNDS: Set[str] = {
    # ==================== Nhóm 操 (thao tác / vận hành) ====================
    "贞操", "操心", "操劳", "操办", "操纵", "操持", "体操", "早操", "晚操",
    "操场", "操练", "操刀", "节操", "操作", "风操",
    # ==================== Nhóm 逼 (ép buộc / bức bách) ====================
    "逼近", "逼迫", "紧逼", "逼人", "威逼", "逼退", "逼真", "逼降", "逼问",
    "倒逼", "逼出", "逼得", "逼走", "逼供",
    # ==================== Nhóm 龟 (rùa) ====================
    "乌龟", "金龟", "海龟", "神龟", "龟缩", "龟壳", "龟速",
    # ==================== Nhóm 奸 (gian / xảo quyệt) ====================
    "奸细", "奸商", "奸雄", "奸诈", "奸贼", "汉奸", "奸佞", "抓奸", "捉奸",
    # ==================== Nhóm 骚 (quấy rối / náo loạn) ====================
    "骚动", "骚乱", "骚扰", "离骚", "骚客",
    # ==================== Nhóm 骨 (xương) ====================
    "骨髓", "深入骨髓", "透骨", "脱胎换骨", "骨肉", "骨气", "骨头", "白骨",
    "露骨", "刻骨铭心",
    # ==================== Nhóm 瘫 (tê liệt) ====================
    "瘫痪", "瘫坐", "瘫倒",
    # ==================== Nhóm 干/幹 (làm / khô / xương sống) ====================
    "干净", "干脆", "干燥", "干涉", "干扰", "干预", "干部", "骨干", "干活",
    "干嘛", "能干", "晒干", "风干", "干杯", "干什么", "干事", "干线", "饼干",
    "若干", "干得好", "干得漂亮", "干得不错", "干得出色", "干劲",
    # ==================== Nhóm 射 (bắn / chiếu) ====================
    "射箭", "射击", "发射", "反射", "照射", "注射", "放射", "辐射", "射线",
    "射程", "映射", "折射", "投射",
    # ==================== Nhóm 插 (cắm / chèn) ====================
    "插花", "插嘴", "插座", "插图", "插曲", "插队", "插手", "插播",
    # -- Từ ghép vô hại BẮT ĐẦU bằng từ explicit (插入=đút vào, 喷水=phun nước) --
    "插入排序", "插入语", "插入法", "插入点", "插入式",
    "喷水池", "喷水壶",
    # ==================== Nhóm 精 (tinh hoa / tinh thần) ====================
    "精神", "精彩", "精华", "精通", "精确", "精力", "酒精", "精心", "精致",
    "精明", "精英", "精细", "精密", "精选", "精品", "精简", "精准", "妖精",
    # ==================== Nhóm 交 (giao / nộp / kết bạn) ====================
    "交通", "交流", "交换", "交易", "社交", "外交", "交叉", "交给", "交朋友",
    "交代", "交接", "交际", "交涉", "交谈", "交付", "交汇",
    # ==================== Nhóm 乳 (sữa / nhũ tương) ====================
    "乳白", "乳酸", "乳制品", "乳名", "乳胶", "乳化", "哺乳动物",
    # ==================== Nhóm 穴 (huyệt đạo / hang động) ====================
    "穴位", "洞穴", "巢穴", "点穴", "穴道",
    # ==================== Nhóm 水 (nước) ====================
    "水平", "山水", "雨水", "泉水", "口水", "水果", "水流量", "水源",
    # ==================== Nhóm 套 (bao / bộ) ====================
    "手套", "外套", "套路", "圈套", "被套", "套装", "配套",
    # ==================== Nhóm 弄 (làm / nắm rõ) ====================
    "弄明白", "弄清楚", "弄丢", "弄堂", "弄错", "弄好", "弄坏",
    # ==================== Nhóm 肉 (thịt / cơ bắp) ====================
    "猪肉", "牛肉", "肉眼", "肌肉", "鸡肉", "肉桂", "肉馅", "果肉",
    # ==================== Nhóm 吹 (thổi) ====================
    "吹风", "吹牛", "吹奏", "吹嘘",
    # ==================== Nhóm 玩 (chơi) ====================
    "玩具", "玩耍", "玩笑", "玩家", "游玩",
    # ==================== Nhóm 情 (tình cảm / tình huống) ====================
    "情感", "情况", "情绪", "事情", "热情", "友情", "亲情", "爱情", "心情",
    "表情", "详情", "剧情", "行情", "人情", "恩情", "豪情", "风情",
    # ==================== Nhóm 欲 (mong muốn / ý muốn / dục) ====================
    "食欲", "求知欲", "望眼欲穿", "随心所欲", "欲言又止", "畅所欲言",
    "为所欲为", "跃跃欲试", "欲盖弥彰", "欲望", "欲念", "求胜欲", "控制欲",
    # ==================== Nhóm 触 / 碰 / 撞 (đụng chạm / va chạm) ====================
    "接触", "碰触", "碰撞", "触碰", "撞见", "相撞", "触动", "触犯", "触觉", "触及", "触摸", "感触", "触目惊心", "碰见", "碰到", "撞倒", "撞碎", "撞击",
    # ==================== Nhóm 爱 (yêu thương / sở thích) ====================
    "热爱", "喜爱", "关爱", "博爱", "友爱", "爱护", "爱惜", "爱心", "可爱", "爱好者",
    # ==================== Nhóm 色 (màu sắc / phong cảnh) ====================
    "颜色", "特色", "神色", "景色", "色彩", "色彩斑斓", "形形色色", "面红耳赤",
    "本色", "角色", "夜色", "春色", "秋色", "天色", "出色",
    # ==================== Nhóm 液 (chất lỏng / y học) ====================
    "液体", "血液", "溶液", "津液", "药液", "汗液", "胃液", "输液", "原液",
    "浆液", "乳化液", "切削液",
    # ==================== Nhóm 洞 (hang động / sâu sắc) ====================
    "黑洞", "山洞", "涵洞", "地洞", "无底洞", "窑洞", "破洞", "洞察", "洞悉", "洞见", "洞房",
    # ==================== Nhóm 棒 (gậy / xuất sắc) ====================
    "木棒", "铁棒", "球棒", "金箍棒", "棒球", "棒极了", "交接棒", "指挥棒", "短棒",
    # ==================== Nhóm 根 (gốc rễ / nguồn gốc) ====================
    "根本", "树根", "根据", "根源", "根底", "草根", "根除", "扎根", "根深蒂固", "寻根", "生根",
    # ==================== Nhóm 茎 (thân thực vật) ====================
    "植物茎", "块茎", "球茎", "根茎", "叶茎", "茎部",
    # ==================== Nhóm 头 (đầu / khởi đầu) ====================
    "回头", "点头", "开头", "尽头", "念头", "舌头", "拳头", "手指头", "领头",
    "眉头", "船头", "桥头", "源头", "苗头", "盼头",
    # ==================== Nhóm 唇 (môi / son môi) ====================
    "唇齿", "唇枪舌剑", "朱唇", "烈焰红唇", "唇膏", "润唇膏", "嘴唇", "上唇", "下唇",
    # ==================== Nhóm 毛 (lông vũ / tóc / len) ====================
    "羽毛", "毛笔", "毛衣", "眉毛", "睫毛", "皮毛", "毛发", "头发", "汗毛",
    "毛皮", "羊毛", "羽绒", "轻如鸿毛", "不毛之地",
    # ==================== Nhóm 胸 (lồng ngực / tấm lòng) ====================
    "胸怀", "胸膛", "胸口", "胸脯", "胸襟", "胸有成竹", "昂首挺胸", "挺胸", "胸围", "胸透",
    # ==================== Nhóm 嫩 (non nớt / mầm cây) ====================
    "鲜嫩", "嫩绿", "嫩叶", "嫩芽", "嫩竹", "娇嫩",
    # ==================== Nhóm 肥 (màu mỡ / phân bón) ====================
    "肥沃", "肥料", "肥美", "肥硕", "肥皂", "施肥", "合肥", "肥胖",
    # ==================== Nhóm 软 (mềm mại / phần mềm) ====================
    "柔软", "软弱", "软件", "软和", "心软", "服软", "欺软怕硬", "软禁",
    # ==================== Nhóm 滑 (trơn trượt / khéo léo) ====================
    "光滑", "滑雪", "滑冰", "狡猾", "滑行", "滑落", "下滑", "滑轮", "圆滑",
    # ==================== Nhóm 湿 (ẩm ướt / khí hậu) ====================
    "湿润", "湿度", "潮湿", "湿地", "湿气", "风湿",
    # ==================== Nhóm 白 (màu trắng / rõ ràng) ====================
    "明白", "白云", "白雪", "清白", "白发", "白天", "白纸", "真相大白", "白费", "白手起家",
    # ==================== Nhóm 屁 (thông thường) ====================
    "放屁", "屁话", "拍马屁",
}

def is_latin_word_char(c: str) -> bool:
    """Kiểm tra ký tự có phải là ký tự từ tiếng Latin/Việt (chữ cái, chữ có dấu, số)."""
    return c.isalnum() and not ('\u4e00' <= c <= '\u9fff')

class TrieNode:
    def __init__(self):
        self.children: Dict[str, TrieNode] = {}
        self.is_end_of_word: bool = False
        self.categories: Set[str] = set()

class LongestMatchTrie:
    def __init__(self):
        self.root = TrieNode()
        self.words: Set[str] = set()

    def insert(self, word: str, category: str = "default"):
        if not word or not word.strip():
            return
        clean_word = word.strip().lower()
        # BẢO VỆ CHỮ HÁN: Từ tiếng Trung bắt buộc phải từ 2 ký tự trở lên (trừ các từ thuần 18+ tuyệt đối như 肏, 屄, 屌)
        EXPLICIT_SINGLE_CHARS = {'肏', '屄', '屌', '尻'}
        if len(clean_word) < 2 and any('\u4e00' <= c <= '\u9fff' for c in clean_word):
            if clean_word not in EXPLICIT_SINGLE_CHARS:
                return
            
        self.words.add(clean_word)
        node = self.root
        for char in clean_word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True
        node.categories.add(category)

    def load_dictionary(self, words: List[str], category: str = "default"):
        for w in words:
            self.insert(w, category)

    def find_all_matches(self, text: str) -> List[Tuple[int, int, str, Set[str]]]:
        if not text:
            return []
        
        matches = []
        text_lower = text.lower()
        n = len(text)
        i = 0
        
        while i < n:
            # KIỂM TRA RANH GIỚI BÊN TRÁI (LEFT BOUNDARY):
            # Với từ Latin/tiếng Việt, không bắt đầu tìm khớp từ vị trí ở giữa một từ khác
            if i > 0 and is_latin_word_char(text_lower[i-1]) and is_latin_word_char(text_lower[i]):
                i += 1
                continue

            node = self.root
            longest_match_len = 0
            longest_categories = set()
            j = i
            
            while j < n and text_lower[j] in node.children:
                node = node.children[text_lower[j]]
                j += 1
                if node.is_end_of_word:
                    is_valid_end = True
                    # KIỂM TRA RANH GIỚI BÊN PHẢI (RIGHT BOUNDARY):
                    # Nếu ký tự cuối và ký tự tiếp theo đều là ký tự Latin/Việt -> không phải kết thúc từ
                    if j < n:
                        prev_char = text_lower[j-1]
                        next_char = text_lower[j]
                        if is_latin_word_char(prev_char) and is_latin_word_char(next_char):
                            is_valid_end = False
                            
                    if is_valid_end:
                        longest_match_len = j - i
                        longest_categories = set(node.categories)
            
            if longest_match_len > 0:
                matched_str = text[i:i + longest_match_len]
                matched_str_lower = matched_str.lower()
                
                # KIỂM TRA BẢO VỆ TỪ AN TOÀN (SAFE COMPOUNDS)
                # Chỉ coi là từ an toàn nếu từ an toàn DÀI HƠN từ khớp (ví dụ: '操' ngắn nằm trong '贞操' hay '骨' nằm trong '骨髓') -> BỎ QUA
                # Nếu từ khớp dài hơn hoặc bằng từ an toàn (ví dụ: '征服的欲望' chứa '欲望'), đó là cụm từ nhạy cảm được cấu hình chủ đích -> BẮT BUỘC MASK
                is_safe_compound = False
                for safe_word in SAFE_COMPOUNDS:
                    if len(safe_word) > longest_match_len and matched_str in safe_word:
                        # Kiểm tra vị trí overlap chính xác của safe_word bao bọc matched_str tại vị trí i
                        safe_len = len(safe_word)
                        start_min = max(0, i - safe_len + 1)
                        for s in range(start_min, min(n - safe_len + 1, i + 1)):
                            if text[s:s + safe_len] == safe_word:
                                is_safe_compound = True
                                break
                        if is_safe_compound:
                            break
                            
                if not is_safe_compound:
                    matches.append((i, i + longest_match_len, matched_str, longest_categories))
                    i += longest_match_len
                else:
                    i += 1
            else:
                i += 1
                
        return matches

