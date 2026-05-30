from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import chess
from config import VOLC_API_KEY

app = Flask(__name__)
board = chess.Board()

client = OpenAI(
    api_key=VOLC_API_KEY,
    base_url="https://ark.cn-beijing.volces.com/api/v3"
)

SYSTEM_PROMPT = """你是一个国际象棋教练，专门辅导有基本规则基础的入门新手。

你的风格：
- 用简单易懂的语言，避免专业术语，必须用术语时要解释
- 重点解释"为什么"，而不只是"应该怎么走"
- 鼓励性的语气，不批评，只引导
- 每次分析控制在200字以内

分析时覆盖三点：
1. 上一步走法的评价
2. 当前局面最需要注意的一件事
3. 建议下一步怎么想"""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/move", methods=["POST"])
def move():
    data = request.get_json(force=True)
    uci = data.get("move")
    try:
        m = chess.Move.from_uci(uci)
        if m in board.legal_moves:
            board.push(m)
            return jsonify({
                "success": True,
                "fen": board.fen(),
                "turn": "white" if board.turn == chess.WHITE else "black",
                "game_over": board.is_game_over(),
                "last_move": uci
            })
        else:
            return jsonify({"success": False, "error": "非法走法"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    fen = data.get("fen")
    last_move = data.get("last_move", "")
    turn = data.get("turn", "")

    try:
        b = chess.Board(fen)
        board_desc = str(b)
    except Exception:
        return jsonify({"error": "FEN 错误"})

    prompt = f"""当前棋局（FEN）：{fen}
棋盘状态：
{board_desc}
上一步走法：{last_move}
现在轮到：{"白方" if turn == "white" else "黑方"}行棋
请分析这个局面。"""

    response = client.chat.completions.create(
        model="doubao-seed-1-8-251228",
        max_tokens=1000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    )
    return jsonify({"analysis": response.choices[0].message.content})


@app.route("/reset", methods=["POST"])
def reset():
    board.reset()
    return jsonify({"fen": board.fen()})


if __name__ == "__main__":
    app.run(debug=True)