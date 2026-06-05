# 模型配置文件
# 要换模型只改这里，其他文件不用动

# 当前使用的模型
CURRENT_MODEL = "deepseek-v4-flash-260425"

# 可选模型（注释掉的是备用）
AVAILABLE_MODELS = {
    # 推荐：速度和质量平衡
   "deepseek-v4-flash-260425": "DeepSeek V4 Flash（推荐）",

    # 不推荐用于教练：太慢
    "doubao-seed-1-8-251228": "豆包 Seed（推理模型，慢）",
}