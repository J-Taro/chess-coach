from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from openai import OpenAI
import chess
import random
import string
from config import VOLC_API_KEY
import threading
import time
from flask import jsonify
from config_models import CURRENT_MODEL
import chess.engine

app = Flask(__name__)
app.secret_key = "chess_coach_secret"
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

client = OpenAI(
    api_key=VOLC_API_KEY,
    base_url="https://ark.cn-beijing.volces.com/api/v3"
)

STOCKFISH_PATH = "/opt/homebrew/bin/stockfish"

rooms = {}
waiting_queue = []

SYSTEM_PROMPT = """你是一个国际象棋教练，专门辅导有基本规则基础的入门新手。

你的风格：
- 分析局面时优先依据 FEN 字符串，FEN 是最准确的棋局表示
- 用简单易懂的语言，避免专业术语，必须用术语时要解释
- 诚实评价，走得好就夸，走得差就直接指出问题所在
- 语气温和但不回避问题，帮助新手真正理解错误
- 每次分析控制在200字以内

输出格式要求：
- 不要使用任何 Markdown 加粗（不要用 ** 包裹文字）
- 不要使用省略号（...）表示黑方走法，直接写棋步名称即可
- 每个分析点之间空一行
- 用以下纯文本格式：

1. 上一步走法评价

[内容]

2. 当前局面最需要注意的一件事

[内容]

3. 建议下一步怎么想

[内容]

分析时覆盖三点：
1. 上一步走法的评价
2. 当前局面最需要注意的一件事
3. 建议下一步怎么想

如果当前局面或走法涉及经典开局、战术或策略，在分析末尾空一行后单独一行标注：
💡 涉及策略：[策略名称]（可自行搜索了解）
没有经典策略则不写这行。

"""


def make_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze_fen", methods=["POST"])
def analyze_fen():
    data = request.get_json(force=True)
    fen = data.get("fen", "")
    history = data.get("history", "")      # 新增：走法历史
    is_review = data.get("is_review", False)  # 新增：是否复盘模式
 
    try:
        board = chess.Board(fen)
    except Exception:
        return jsonify({"error": "FEN 格式有误"})
 
    turn = "白方" if board.turn == chess.WHITE else "黑方"
 
    try:
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            result = engine.analyse(board, chess.engine.Limit(time=0.5))
            best_move = result["pv"][0] if result.get("pv") else None
            best_move_uci = best_move.uci() if best_move else "无"
            score = result["score"].white()
            if score.is_mate():
                score_str = f"白方将死黑方还需 {score.mate()} 步" if score.mate() > 0 else f"黑方将死白方还需 {abs(score.mate())} 步"
            else:
                cp = score.score()
                if cp > 0:
                    score_str = f"白方优势 {cp/100:.1f} 分"
                elif cp < 0:
                    score_str = f"黑方优势 {abs(cp)/100:.1f} 分"
                else:
                    score_str = "局面均势"
    except Exception:
        best_move_uci = "无"
        score_str = "评估失败"
 
    # 根据模式构建不同 prompt
    if is_review and history:
        prompt = f"""这是一局刚结束的对局，请帮玩家复盘。
 
完整走法历史（UCI格式）：{history}
最终局面 FEN：{fen}
Stockfish 评估：{score_str}
 
请按以下格式复盘：
 
1. 整局总体评价
[简短评价这局棋的整体走势]
 
2. 最关键的失误
[指出最影响结果的1-2步错误，说明为什么错]
 
3. 做得好的地方
[指出1-2步走得不错的地方]
 
4. 下次重点注意
[给出一个具体的改进建议]"""
    else:
        prompt = f"""当前棋局 FEN：{fen}
现在轮到：{turn}行棋
{"完整走法历史：" + history if history else ""}
 
Stockfish引擎评估：
- 当前局面：{score_str}
- 引擎推荐走法：{best_move_uci}
 
请分析这个局面，在建议下一步时必须包含引擎推荐的走法 {best_move_uci} 并用人话解释为什么这步好。"""
 
    def generate():
        response = client.chat.completions.create(
            model=CURRENT_MODEL,
            max_tokens=500,  # 复盘需要更多 token
            stream=True,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        )
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                escaped = delta.replace("\n", "||n||")
                yield f"data: {escaped}\n\n"
        yield "data: [DONE]\n\n"
 
    return app.response_class(generate(), mimetype="text/event-stream")
 
 
@app.route("/stockfish_move", methods=["POST"])
def stockfish_move():
    """接收 FEN，返回 Stockfish 的走法（供人机对战用）"""
    data = request.get_json(force=True)
    fen = data.get("fen", "")
    depth = data.get("depth", 8)
 
    try:
        board = chess.Board(fen)
    except Exception:
        return jsonify({"error": "FEN 格式有误"})
 
    if board.is_game_over():
        return jsonify({"move": None, "game_over": True})
 
    try:
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            result = engine.play(board, chess.engine.Limit(depth=depth))
            move_uci = result.move.uci()
            analysis = engine.analyse(board, chess.engine.Limit(depth=depth))
            score = analysis["score"].white()
            if score.is_mate():
                score_str = f"将死还需 {abs(score.mate())} 步"
            else:
                cp = score.score()
                score_str = f"{cp/100:+.1f}"
 
        return jsonify({
            "move": move_uci,
            "score": score_str,
            "game_over": False
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@socketio.on("create_room")
def on_create_room(data):
    code = make_room_code()
    color_pref = data.get("color", "random")
    if color_pref == "random":
        my_color = random.choice(["white", "black"])
    else:
        my_color = color_pref
    time_ctrl = data.get("time_control", {"minutes": 0, "increment": 0})
    minutes = time_ctrl.get("minutes") or 0
    increment = time_ctrl.get("increment") or 0
    rooms[code] = {
        "turn_start": None,
        "board": chess.Board(),
        "players": {request.sid: my_color},
        "creator_color": my_color,
        "last_move": "",
        "history": [],
        "minutes": minutes,
        "increment": increment,
        "clocks": {
            "white": minutes * 60,
            "black": minutes * 60
        },
        "unlimited": minutes == 0
    }
    join_room(code)
    emit("room_created", {"code": code, "color": my_color})


@socketio.on("join_room_code")
def on_join_room(data):
    code = data.get("code", "").upper()
    if code not in rooms:
        emit("error", {"msg": "房间不存在"})
        return
    room = rooms[code]

    # 有断线记录，允许重连
    if "disconnected" in room:
        old_sid = room["disconnected"]
        old_color = room["players"].get(old_sid)
        del room["players"][old_sid]
        del room["disconnected"]
        room["players"][request.sid] = old_color
        join_room(code)
        emit("joined_room", {"code": code, "color": old_color})
        emit("game_start", {
            "fen": room["board"].fen(),
            "unlimited": room["unlimited"],
            "white_time": room["clocks"]["white"],
            "black_time": room["clocks"]["black"],
            "increment": room["increment"]
        }, to=code)
        return

    if len(room["players"]) >= 2:
        emit("error", {"msg": "房间已满"})
        return

    creator_color = room["players"][list(room["players"].keys())[0]]
    joiner_color = "black" if creator_color == "white" else "white"
    room["players"][request.sid] = joiner_color
    join_room(code)
    emit("joined_room", {"code": code, "color": joiner_color})
    room["turn_start"] = time.time()
    emit("game_start", {
        "fen": room["board"].fen(),
        "unlimited": room["unlimited"],
        "white_time": room["clocks"]["white"],
        "black_time": room["clocks"]["black"],
        "increment": room["increment"]
    }, to=code)


@socketio.on("quick_match")
def on_quick_match(data):
    time_ctrl = data.get("time_control", {"minutes": 0, "increment": 0})
    if waiting_queue:
        other_sid, _ = waiting_queue.pop(0)
        code = make_room_code()
        minutes = time_ctrl.get("minutes", 0)
        increment = time_ctrl.get("increment", 0)
        rooms[code] = {
            "turn_start": None,
            "board": chess.Board(),
            "players": {other_sid: "white", request.sid: "black"},
            "last_move": "",
            "history": [],
            "minutes": minutes,
            "increment": increment,
            "clocks": {"white": minutes * 60, "black": minutes * 60},
            "unlimited": minutes == 0
        }
        join_room(code)
        socketio.server.enter_room(other_sid, code)
        emit("matched", {"code": code, "color": "black"})
        emit("matched", {"code": code, "color": "white"}, to=other_sid)
        emit("game_start", {
            "fen": rooms[code]["board"].fen(),
            "unlimited": rooms[code]["unlimited"],
            "white_time": rooms[code]["clocks"]["white"],
            "black_time": rooms[code]["clocks"]["black"],
            "increment": rooms[code]["increment"]
        }, to=code)
    else:
        waiting_queue.append((request.sid, time_ctrl))
        emit("waiting", {})


@socketio.on("make_move")
def on_move(data):
    code = data.get("room")
    uci = data.get("move")
    if code not in rooms:
        return
    room = rooms[code]
    board = room["board"]
    color = room["players"].get(request.sid)
    turn = "white" if board.turn == chess.WHITE else "black"
    if color != turn:
        emit("error", {"msg": "还没轮到你"})
        return
    try:
        move = chess.Move.from_uci(uci)
        if move in board.legal_moves:
            if not room["unlimited"]:
                now = time.time()
                elapsed = now - (room["turn_start"] or now)
                room["clocks"][color] -= elapsed
                room["clocks"][color] += room["increment"]
                room["turn_start"] = now
                if room["clocks"][color] <= 0:
                    room["clocks"][color] = 0
                    emit("timeout", {"loser": color}, to=code)
                    del rooms[code]
                    return
            board.push(move)
            room["history"].append(uci)
            room["last_move"] = uci
            emit("move_made", {
                "fen": board.fen(),
                "move": uci,
                "game_over": board.is_game_over(),
                "white_time": room["clocks"]["white"],
                "black_time": room["clocks"]["black"],
                "server_time": time.time()
            }, to=code)
        else:
            emit("error", {"msg": "非法走法"})
    except Exception as e:
        emit("error", {"msg": str(e)})


@socketio.on("timeout")
def on_timeout(data):
    code = data.get("room")
    color = data.get("color")
    if code in rooms:
        emit("timeout", {"loser": color}, to=code)
        del rooms[code]


@socketio.on("analyze")
def on_analyze(data):
    code = data.get("room")
    if code not in rooms:
        return
    room = rooms[code]
    board = room["board"]
    fen = board.fen()
    last_move = room["last_move"]
    history_str = " → ".join(room["history"]) if room["history"] else "无"
    current_turn = "白方" if board.turn == chess.WHITE else "黑方"
    last_turn = "黑方" if board.turn == chess.WHITE else "白方"
    requester_color = room["players"].get(request.sid)
    requester = "白方" if requester_color == "white" else "黑方"

    # 用 Stockfish 算最优走法和局面评分
    try:
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            result = engine.analyse(board, chess.engine.Limit(time=0.5))
            best_move = result["pv"][0] if result.get("pv") else None
            best_move_uci = best_move.uci() if best_move else "无"
            score = result["score"].white()
            if score.is_mate():
                score_str = f"白方将死黑方还需 {score.mate()} 步" if score.mate() > 0 else f"黑方将死白方还需 {abs(score.mate())} 步"
            else:
                cp = score.score()
                if cp > 0:
                    score_str = f"白方优势 {cp/100:.1f} 分"
                elif cp < 0:
                    score_str = f"黑方优势 {abs(cp)/100:.1f} 分"
                else:
                    score_str = "局面均势"
    except Exception as e:
        best_move_uci = "无"
        score_str = "评估失败"

    prompt = f"""当前棋局（FEN）：{fen}
完整走法历史（UCI格式）：{history_str}
上一步走法：{last_move}（{last_turn}走的）
现在轮到：{current_turn}行棋
请求分析的玩家是：{requester}

Stockfish引擎评估：
- 当前局面：{score_str}
- 引擎推荐走法：{best_move_uci}

请站在{requester}的角度，结合引擎评估给出分析和建议。
在建议下一步时，必须包含引擎推荐的走法 {best_move_uci} 并用人话解释为什么这步好。"""

    def stream_in_thread():
        response = client.chat.completions.create(
            model=CURRENT_MODEL,
            max_tokens=400,
            stream=True,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        )
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                socketio.emit("analysis_chunk", {"text": delta}, to=request.sid)
        socketio.emit("analysis_done", {}, to=request.sid)

    threading.Thread(target=stream_in_thread).start()


import threading

@socketio.on("disconnect")
def on_disconnect():
    if any(sid == request.sid for sid, _ in waiting_queue):
        waiting_queue[:] = [(s, t) for s, t in waiting_queue if s != request.sid]

    for code, room in list(rooms.items()):
        if request.sid in room["players"]:
            room["disconnected"] = request.sid
            socketio.emit("opponent_disconnected", {
                "msg": "对手已断线，等待重连..."
            }, to=code, namespace="/")
            break


@socketio.on("reconnect_room")
def on_reconnect_room(data):
    code = data.get("code")
    color = data.get("color")
    if code not in rooms:
        emit("error", {"msg": "房间已关闭"})
        return
    room = rooms[code]
    
    # 找到断线的旧 sid 并替换
    old_sid = room.get("disconnected")
    if old_sid and old_sid in room["players"]:
        del room["players"][old_sid]
    
    room["players"][request.sid] = color
    if "disconnected" in room:
        del room["disconnected"]
    join_room(code)
    emit("reconnected", {
        "fen": room["board"].fen(),
        "white_time": room["clocks"]["white"],
        "black_time": room["clocks"]["black"],
        "unlimited": room["unlimited"]
    })


if __name__ == "__main__":
    socketio.run(app, debug=True)