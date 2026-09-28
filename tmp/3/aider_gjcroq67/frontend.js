/**
 * Tic Tac Toe vs AI
 * Human is "X", AI is "O"
 */

let gameBoard = [
    ["", "", ""],
    ["", "", ""],
    ["", "", ""]
];
let gameStatus = "Your turn"; // "Your turn", "AI turn", "X wins", "O wins", "Draw"
let gameOver = false;

function log(...args) {
    // For debugging
    // console.log(...args);
}

function renderBoard() {
    const boardDiv = document.getElementById("board");
    boardDiv.innerHTML = "";
    for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 3; col++) {
            const cellBtn = document.createElement("button");
            cellBtn.className = "board-cell";
            cellBtn.dataset.row = row;
            cellBtn.dataset.col = col;
            cellBtn.textContent = gameBoard[row][col];
            cellBtn.disabled = !!gameBoard[row][col] || gameOver || gameStatus !== "Your turn";
            cellBtn.addEventListener("click", handleCellClick);
            boardDiv.appendChild(cellBtn);
        }
    }
}

function updateStatus() {
    const statusDiv = document.getElementById("game-status");
    statusDiv.textContent = "Game Status: " + gameStatus;
}

function handleCellClick(e) {
    if (gameOver || gameStatus !== "Your turn") return;
    const row = parseInt(e.target.dataset.row);
    const col = parseInt(e.target.dataset.col);
    if (gameBoard[row][col] !== "") return;

    gameBoard[row][col] = "X";
    log("Human move:", row, col);
    checkGameState();
    if (!gameOver) {
        gameStatus = "AI turn";
        updateStatus();
        renderBoard();
        setTimeout(aiMove, 500);
    } else {
        updateStatus();
        renderBoard();
    }
}

function aiMove() {
    // Simple AI: pick random empty cell
    const emptyCells = [];
    for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 3; col++) {
            if (gameBoard[row][col] === "") {
                emptyCells.push({ row, col });
            }
        }
    }
    if (emptyCells.length === 0) return;
    // Try to win or block
    const move = findBestMove("O") || findBestMove("X") || emptyCells[Math.floor(Math.random() * emptyCells.length)];
    gameBoard[move.row][move.col] = "O";
    log("AI move:", move.row, move.col);
    checkGameState();
    if (!gameOver) {
        gameStatus = "Your turn";
    }
    updateStatus();
    renderBoard();
}

function findBestMove(player) {
    // Try to win (player="O") or block (player="X")
    for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 3; col++) {
            if (gameBoard[row][col] === "") {
                gameBoard[row][col] = player;
                if (checkWinnerOnBoard(gameBoard) === player) {
                    gameBoard[row][col] = "";
                    return { row, col };
                }
                gameBoard[row][col] = "";
            }
        }
    }
    return null;
}

function checkGameState() {
    const winner = checkWinnerOnBoard(gameBoard);
    if (winner) {
        gameStatus = winner === "X" ? "You win!" : "AI wins!";
        gameOver = true;
    } else if (isBoardFull()) {
        gameStatus = "Draw";
        gameOver = true;
    }
}

function checkWinnerOnBoard(board) {
    // Rows
    for (let row = 0; row < 3; row++) {
        if (board[row][0] && board[row][0] === board[row][1] && board[row][1] === board[row][2]) {
            return board[row][0];
        }
    }
    // Columns
    for (let col = 0; col < 3; col++) {
        if (board[0][col] && board[0][col] === board[1][col] && board[1][col] === board[2][col]) {
            return board[0][col];
        }
    }
    // Diagonals
    if (board[0][0] && board[0][0] === board[1][1] && board[1][1] === board[2][2]) {
        return board[0][0];
    }
    if (board[0][2] && board[0][2] === board[1][1] && board[1][1] === board[2][0]) {
        return board[0][2];
    }
    return null;
}

function isBoardFull() {
    for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 3; col++) {
            if (gameBoard[row][col] === "") return false;
        }
    }
    return true;
}

function resetGame() {
    gameBoard = [
        ["", "", ""],
        ["", "", ""],
        ["", "", ""]
    ];
    gameStatus = "Your turn";
    gameOver = false;
    updateStatus();
    renderBoard();
}

document.getElementById("play-again-btn").addEventListener("click", resetGame);

window.addEventListener("DOMContentLoaded", () => {
    updateStatus();
    renderBoard();
});