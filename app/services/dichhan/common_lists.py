"""
TẬP HỢP TỪ VỰNG DÙNG CHUNG TỐI ƯU BỘ NHỚ (OPTIMIZED SHARED CONSTANTS)
Sử dụng frozenset để nạp 1 LẦN DUY NHẤT vào RAM khi ứng dụng khởi chạy.
Đảm bảo tốc độ tra cứu O(1), không nhân bản bộ nhớ, chống rò rỉ RAM tuyệt đối.
"""

# Bộ từ vựng Pinyin tiêu chuẩn (~400 âm tiết) - FrozenSet O(1)
PINYIN_SYLLABLES = frozenset({
    "a", "ai", "an", "ang", "ao", "ba", "bai", "ban", "bang", "bao", "bei", "ben", "beng", "bi", "bian", 
    "biao", "bie", "bin", "bing", "bo", "bu", "ca", "cai", "can", "cang", "cao", "ce", "cen", "ceng", 
    "cha", "chai", "chan", "chang", "chao", "che", "chen", "cheng", "chi", "chong", "chou", "chu", "chua", 
    "chuai", "chuan", "chuang", "chui", "chun", "chuo", "ci", "cong", "cou", "cu", "cuan", "cui", "cun", 
    "cuo", "da", "dai", "dan", "dang", "dao", "de", "dei", "deng", "di", "dian", "diao", "die", "ding", 
    "diu", "dong", "dou", "du", "duan", "dui", "dun", "duo", "e", "ei", "en", "eng", "er", "fa", "fan", 
    "fang", "fei", "fen", "feng", "fo", "fou", "fu", "ga", "gai", "gan", "gang", "gao", "ge", "gei", 
    "gen", "geng", "gong", "gou", "gu", "gua", "guai", "guan", "guang", "gui", "gun", "guo", "ha", "hai", 
    "han", "hang", "hao", "he", "hei", "hen", "heng", "hong", "hou", "hu", "hua", "huai", "huan", 
    "huang", "hui", "hun", "huo", "ji", "jia", "jian", "jiang", "jiao", "jie", "jin", "jing", "jiong", 
    "jiu", "ju", "juan", "jue", "jun", "ka", "kai", "kan", "kang", "kao", "ke", "kei", "ken", "keng", 
    "kong", "kou", "ku", "kua", "kuai", "kuan", "kuang", "kui", "kun", "kuo", "la", "lai", "lan", 
    "lang", "lao", "le", "lei", "leng", "li", "lia", "lian", "liang", "liao", "lie", "lin", "ling", 
    "liu", "long", "lou", "lu", "lü", "luan", "lue", "lüe", "lun", "luo", "ma", "mai", "man", 
    "mang", "mao", "me", "mei", "men", "meng", "mi", "mian", "miao", "mie", "min", "ming", "miu", 
    "mo", "mou", "mu", "na", "nai", "nan", "nang", "nao", "ne", "nei", "nen", "neng", "ni", 
    "nian", "niang", "niao", "nie", "nin", "ning", "niu", "nong", "nou", "nu", "nü", "nuan", 
    "nue", "nüe", "nun", "nuo", "o", "ou", "pa", "pai", "pan", "pang", "pao", "pei", "pen", 
    "peng", "pi", "pian", "piao", "pie", "pin", "ping", "po", "pou", "pu", "qi", "qia", "qian", 
    "qiang", "qiao", "qie", "qin", "qing", "qiong", "qiu", "qu", "quan", "que", "qun", "ran", 
    "rang", "rao", "re", "ren", "reng", "ri", "rong", "rou", "ru", "ruan", "rui", "run", "ruo", 
    "sa", "sai", "san", "sang", "sao", "se", "sen", "seng", "sha", "shai", "shan", "shang", 
    "shao", "she", "shei", "shen", "sheng", "shi", "shou", "shu", "shua", "shuai", "shuan", 
    "shuang", "shui", "shun", "shuo", "si", "song", "sou", "su", "suan", "sui", "sun", "suo", 
    "ta", "tai", "tan", "tang", "tao", "te", "teng", "ti", "tian", "tiao", "tie", "ting", 
    "tong", "tou", "tu", "tuan", "tui", "tun", "tuo", "wa", "wai", "wan", "wang", "wei", 
    "wen", "weng", "wo", "wu", "xi", "xia", "xian", "xiang", "xiao", "xie", "xin", "xing", 
    "xiong", "xiu", "xu", "xuan", "xue", "xun", "ya", "yan", "yang", "yao", "ye", "yi", 
    "yin", "ying", "yo", "yong", "you", "yu", "yuan", "yue", "yun", "za", "zai", "zan", 
    "zang", "zao", "ze", "zei", "zen", "zeng", "zha", "zhai", "zhan", "zhang", "zhao", 
    "zhe", "zhei", "zhen", "zheng", "zhi", "zhong", "zhou", "zhu", "zhua", "zhuai", 
    "zhuan", "zhuang", "zhui", "zhun", "zhuo", "zi", "zong", "zou", "zu", "zuan", "zui", 
    "zun", "zuo"
})

# Danh hiệu / hậu tố thông dụng của truyện
HONORIFICS = frozenset({
    "lão", "tiểu", "cô nương", "tiểu thư", "gia chủ", "điện chủ", "tông chủ", 
    "thánh nữ", "thánh tử", "sư huynh", "sư tỷ", "sư đệ", "sư muội", "huynh", "tỷ", "muội"
})

# Tập hợp các từ tiếng Việt dừng thông dụng nhất
VIETNAMESE_STOPWORDS = frozenset({
    "đứng", "ngồi", "ăn", "uống", "đẹp", "cao", "thấp", "nói", "cười", "đi", "đến", "với", "cho", 
    "ngày", "đêm", "trong", "ngoài", "trên", "dưới", "không", "nhìn", "nghe", "thấy", "làm", 
    "nhưng", "cũng", "thế", "này", "vẫn", "đang", "đã", "sẽ", "được", "bị", "có", "là", "và", 
    "của", "để", "ra", "vào", "lên", "xuống", "qua", "lại", "nơi", "nhiều", "ít", "một", "hai",
    "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười", "người", "nhà", "cửa", "cây", "lá",
    "cái", "con", "nó", "chúng", "tôi", "anh", "em", "ông", "bà", "chị", "họ", "ta", "mình",
    "đâu", "nào", "sao", "xin", "cơ", "quá", "lắm", "rất", "hơn", "như", "nhất", "chỉ", "vừa"
})

CHINESE_SURNAMES = (
    "萧", "林", "叶", "顾", "陈", "李", "张", "王", "刘", "杨", "赵", "黄", "周", "吴", "徐", "孙", "胡", "朱", "高", "莫", "楚", "唐", "陆", "韩", "苏", "秦", "沈", "白", "江", "谢", "宋", "程", "曹", "魏", "罗", "梁", "何", "郭", "董", "郑", "任", "薛", "谭", "阎", "潘", "丁", "姜", "崔", "孟", "段", "雷", "钱", "尹", "黎", "易", "龙", "武", "乔", "桑", "石", "古", "文",
    # Bổ sung các họ lớn trong Bách Gia Tính & Thủy Hử / Kiếm hiệp
    "杜", "柴", "阮", "卢", "戴", "史", "鲁", "燕", "花", "索", "裴", "贾", "钟", "田", "陶", "邹", "穆", "解", "童", "樊", "鲍", "项", "龚", "焦", "郁", "施", "孔", "严", "金", "尤", "许", "吕", "戚", "柏", "水", "窦", "章", "戚", "谢", "邹", "喻", "柏", "水", "窦", "章", "云", "苏", "潘", "葛", "奚", "范", "彭", "郎", "鲁", "韦", "昌", "马", "苗", "凤", "花", "方", "俞", "任", "袁", "柳", "酆", "鲍", "史", "唐", "费", "廉", "岑", "薛", "雷", "贺", "倪", "汤", "滕", "殷", "罗", "毕", "郝", "邬", "安", "常", "乐", "于", "时", "傅", "皮", "卞", "齐", "康", "伍", "余", "元", "卜", "顾", "孟", "平", "黄", "和", "穆", "萧", "尹", "姚", "邵", "湛", "汪", "祁", "毛", "禹", "狄", "米", "贝", "明", "臧", "计", "伏", "成", "戴", "谈", "宋", "茅", "庞", "熊", "纪", "舒", "屈", "项", "祝", "董", "梁",
    "欧阳", "司马", "上官", "诸葛", "东方", "独孤", "南宫", "令狐", "公孙", "百里", "拓跋", "宇文", "皇甫", "慕容", "完颜", "长孙", "尉迟", "司徒", "司空", "端木", "呼延", "斡尔", "乞颜", "布", "伊", "麦", "亚", "索", "卡", "莱", "克", "鲁", "艾"
)

# Tiền tố thân mật / biệt danh / bối phận (NER)
TITLE_PREFIXES = (
    "小", "老", "阿", "大"
)

# Hậu tố chức danh / xưng hô / gia đình / biệt danh / chức vụ dị giới (NER)
TITLE_SUFFIXES = (
    "师兄", "师姐", "师弟", "师妹", "师父", "师尊", "师傅", "长老", "宗主", "掌门", "殿主", "峰主", "门主", "阁主", "城主", 
    "道友", "皇子", "公主", "少爷", "小姐", "老祖", "大帝", "神君", "仙子", "圣女", "圣子", "王爷", "皇叔",
    "哥", "姐", "弟", "妹", "叔", "伯", "姨", "嫂", "总", "董", "总监", "经理", "主任", "院长", "校董", "教授", "医生",
    "兄", "氏", "爷", "奶", "儿", "大人", "先生", "前辈", "夫人", "娘", "丫头",
    "爸", "妈", "爷爷", "奶奶", "公", "婆", "子", "侯", "娃", "崽",
    "议长", "议员", "参议长", "领主", "总督", "统帅", "统领", "会长", "执事", "主祭", "祭司", "大公", "公爵", "侯爵", "伯爵", "魔皇", "魔王", "魔尊", "魔将", "天尊", "天君",
    "神君", "真仙", "道祖", "灵神"
)

# Ngoại hiệu, đạo hiệu, danh xưng giang hồ, biệt hiệu hảo hán 2-4 chữ (Thủy Hử, Kiếm hiệp, Tiên hiệp, Thần thoại)
EPITHET_SUFFIXES = (
    "金刚", "秀士", "天王", "太保", "二郎", "三郎", "忽律", "先锋", "和尚", "行者", 
    "阎罗", "太岁", "旋风", "神医", "道人", "真君", "仙子", "散人", "狂客", "剑客", 
    "剑仙", "剑圣", "刀客", "魔君", "鬼王", "龙王", "大王", "霸王",
    "居士", "掌门", "舵主", "星君", "判官", "无常", "煞神", "罗汉", "飞将", "神枪",
    "神手", "书生", "剑神", "剑宗", "刀狂", "拳王", "怪侠", "狂生",
    "着天", "遮天", "通天", "擎天", "翻天", "灵神", "门神", "翅虎", "面虎", "毛虎", "涧虎", "云龙", "江龙", "纹龙", "子头", "尾蝎", "花蛇"
)

# Tiền tố ngữ cảnh nhận diện Địa danh / Thế giới (Contextual Place Prefixes)
CONTEXT_PLACE_PREFIXES = (
    "这里是", "这是", "来到", "前往", "身在", "位于", "穿梭至", "踏入", "降临", "回到了", "在", "身处"
)

# Hậu tố compound entity — Phát hiện Địa danh, Tông môn, Vật phẩm, Chiêu thức (NER trải khắp 7 thể loại)
ENTITY_COMPOUND_SUFFIXES = {
    "PLACE": (
        # Cổ phong & Tiên hiệp & Sơn trại Lục lâm
        "城", "宫", "殿", "山", "谷", "峰", "海", "域", "界", "洲", "省",
        "县", "关", "岛", "村", "河", "江", "潭", "原", "镇", "府", "坊",
        "楼", "阁", "塔", "洞", "渊", "林", "园", "寺", "庙", "窟", "寨",
        "堡", "山庄", "崖", "渡", "池", "泉", "亭", "峡", "陵", "坡", "冈", "洼", "泊", "滩",
        # Đô thị & Hiện đại & Khoa huyễn / Tinh tế
        "星", "世界", "大厦", "大楼", "广场", "公园", "花园", "小区", "华苑", "公馆", "公寓",
        "大桥", "立交桥", "路", "街", "大道", "胡同", "巷", "码头", "港", "空港", "基地", "避难所",
        "空间站", "要塞", "遗迹", "星系", "星区"
    ),
    "SECT": (
        "宗", "门", "派", "帮", "教", "盟", "会", "庄", "院", "堂",
        "集团", "公司", "族", "家", "世家", "镖局", "商会", "神教",
        "洞天", "福地", "皇朝", "王朝", "神朝", "帝国", "公会", "军团", "基地", "联盟", "议会", "神殿"
    ),
    "ITEM": (
        # Binh khí cổ điển & Huyền huyễn & Lục lâm
        "剑", "刀", "枪", "戟", "弓", "扇", "琴", "甲", "珠", "镜",
        "丹", "符", "鼎", "瓶", "铠", "轮", "令", "图", "环",
        "戒", "袍", "旗", "膏", "丸", "散", "草", "花", "果", "木",
        "针", "棒", "杖", "鞭", "索", "尺", "印", "幡", "佩", "玉", "炉", "钟",
        "矛", "盾", "斧", "钺", "钩", "叉", "钯", "锤", "槊", "棍", "匕", "锏", "匕首", "箭",
        # Khoa huyễn / Cơ giáp / Mạt thế / Rượu thịt dân gian
        "机甲", "战甲", "光剑", "光刃", "核芯", "核心", "晶核", "晶石", "源石", "能晶",
        "香", "酒", "露", "酿"
    ),
    "SKILL": (
        "掌", "拳", "指", "功", "诀", "经", "术", "阵", "法", "印",
        "步", "体", "腿", "爪", "斩", "剑法", "身法", "心法", "吟",
        "式", "招", "技", "圈", "神功", "大法", "真经", "刀法", "指法",
        "枪法", "棍法", "杖法", "鞭法", "神剑", "宝典", "秘籍", "图录",
        "剑谱", "绝技", "秘法", "神通", "掌法", "腿法", "爪法", "遁法",
        "剑气", "剑意", "刀意", "拳意", "真气", "异能", "领域", "结界"
    ),
    "CREATURE": (
        # Dị thú, Thần thú, Linh dị, Quái vật, Sinh vật lạ (7 thể loại)
        "兽", "妖", "怪", "魔", "鬼", "尸", "精", "煞", "灵", "祟", "邪",
        "猴", "猿", "蟒", "蛇", "狼", "虎", "豹", "熊", "雕", "鹰", "雀", "鸟", "鹫", "鹏", "乌", "鸾",
        "龙", "凤", "麟", "鳌", "龟", "蝠", "蛊", "虫", "蛛", "蝎", "蚁", "蝶",
        "丧尸", "变异兽", "凶兽", "异兽", "神兽", "妖兽", "魔兽", "母体"
    ),
    "BOOK_CANON": (
        # Kinh thư, Tác phẩm, Điển tịch, Thơ văn (Bắt kể cả khi không có ngoặc 《》)
        "书", "传", "录", "志", "谱", "篇", "卷", "图", "赋", "经", "典", "集"
    ),
    "ERA_PERIOD": (
        # Thời kỳ, thời đại lịch sử & bối cảnh thế giới quan
        "时期", "時代", "时代", "纪元", "岁月", "年间"
    )
}

# Hậu tố tên dân dã, nhũ danh, biệt danh thôn quê (Folk Name Suffixes)
FOLK_NAME_SUFFIXES = (
    "头", "子", "柱", "娃", "妞", "蛋", "儿", "猴", "仔", "狗", "剩", "丫"
)

# Thực thể dân gian, nghề nghiệp cổ truyền, ma mị, tâm linh (Folk & Supernatural Entities)
FOLK_OCCUPATION_ENTITIES = (
    "捞尸人", "捞尸匠", "捞尸夫", "扎纸人", "扎纸匠", "缝尸人", "缝尸匠",
    "赶尸人", "赶尸匠", "背尸人", "背尸匠", "抬棺人", "抬棺匠", "守陵人", "守夜人",
    "仵作", "阴阳先生", "风水先生", "出马仙", "保家仙", "五大仙",
    "黄大仙", "狐仙", "柳仙", "白仙", "灰仙", "胡三太爷", "黄二大爷",
    "水猴子", "水猴儿", "水鬼", "水底怪", "落水鬼", "替死鬼", "俗主", "红白煞",
    "白无常", "黑无常", "牛头", "马面", "孟婆", "阎王", "判官",
    "借寿人", "赊刀人", "打更人", "刽子手"
)

# Ký tự động từ / liên từ / giới từ kết thúc câu hội thoại hay dính sau tên
VERB_TRAILING_CHARS = frozenset({
    "问", "道", "说", "喊", "叫", "笑", "怒", "叹", "骂", "哭", "答", "讲", 
    "指", "看", "听", "见", "望", "瞧", "走", "去", "来", "到", "出", "进",
    "站", "坐", "倒", "躺", "跑", "追", "抓", "拉", "提", "拿", "抱", "打",
    "回", "应", "想", "知", "觉", "感", "恨", "怕", "死", "生", "活",
    "及", "同", "与", "而", "但", "却", "又", "且", "并", "因", "为", "所",
    "以", "让", "使", "便", "就", "将", "向", "自", "从", "由", "给", "被"
})

# Giới từ / liên từ / hư từ dính liền trước tên riêng cần bóc tách
LEADING_STRIP_PARTICLES = frozenset([
    "和", "与", "跟", "同", "及", "的", "在", "是", "那", "这", "有", "个", 
    "被", "对", "从", "由", "见", "了", "向", "正", "将", "让", "但", "便", "就", "又", "也"
])


