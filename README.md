# Sammy Discord Bot

這是一個以 Python + `discord.py` 製作的模組化 Discord Bot。**Bot 在 Discord 中顯示的指令、表單、狀態文字與 AI 預設回覆均為英文**；本說明文件以繁體中文撰寫，方便部署。

## 功能

1. **Welcome（新成員歡迎）**：可設定歡迎頻道、訊息模板、啟用/停用；可設定 HTTPS 背景圖、啟用/停用圖片，並選擇新成員頭像在左／中／右。頭像會裁切成圓形並加上珊瑚紅色外框。
2. **AI Auto-reply（AI 頻道自動回覆）**：指定一個文字頻道，Bot 會對該頻道內每則非 Bot 的文字訊息呼叫 OpenAI 相容 API 回覆；可自訂系統提示詞／人格，並保存有限長度的頻道對話紀錄。
3. **Auto Role（自動身分組）**：新成員加入時，自動授予設定好的身分組。
4. **資料持久化**：Render 部署使用 Supabase PostgreSQL；本機開發若沒有設定 `DATABASE_URL`，會自動使用 SQLite。
5. **健康檢查頁面**：`/healthz`，供 Render Web Service 檢查服務是否正在執行。

## 專案結構

```text
main.py                 啟動 Discord Bot 與 HTTP 健康檢查伺服器
 database.py            PostgreSQL / SQLite 資料存取
 image_card.py          歡迎圖片製作
 cogs/welcome.py        歡迎訊息指令與加入事件
 cogs/ai_chat.py        AI 指令與頻道自動回覆
 cogs/autorole.py       自動身分組指令與加入事件
 tests/test_database.py 資料庫基本測試
 requirements.txt       Python 套件
 .env.example           環境變數範例
 render.yaml            Render 免費 Web Service Blueprint
```

## 一、建立 Discord Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)，建立 Application。
2. 進入 **Bot**，建立或重設 Bot Token。Token 是密碼，不能貼給任何人或上傳 GitHub。
3. 在 **Bot → Privileged Gateway Intents** 開啟：
   - **Server Members Intent**：歡迎訊息與自動身分組必須開啟。
   - **Message Content Intent**：AI 頻道讀取訊息文字必須開啟。
4. 到 **OAuth2 → URL Generator**，勾選 `bot` 與 `applications.commands`。
5. Bot 權限至少勾選：
   - View Channels
   - Send Messages
   - Read Message History
   - Attach Files
   - Manage Roles（需要自動身分組時）
6. 使用產生的邀請連結，把 Bot 加入你的伺服器。
7. 若使用自動身分組，進入伺服器 **Server Settings → Roles**，把 Bot 的最高身分組移到目標身分組上方。

## 二、免費部署架構

本專案預設使用：

- **GitHub**：存放程式碼（建議建立 Private repository）。
- **Render Free Web Service**：執行 Python Bot，同時提供 `/healthz`。
- **Supabase Free PostgreSQL**：保存各伺服器設定與 AI 對話紀錄。
- **Groq 或其他 OpenAI 相容 API**：提供 AI 回覆。API 是否免費、可用模型與使用額度依供應商帳戶及當時政策而定。

### Render 免費方案的重要限制

- Render Free Web Service 在 15 分鐘沒有收到任何入站 HTTP 請求或既有 WebSocket 訊息時會休眠；本 Bot 會維持 Discord Gateway WebSocket 連線。Render 在 2026-02-24 更新後，既有 WebSocket 連線收到訊息也會計入活動，因此 Discord Gateway 的訊息流量可讓服務維持運作。
- 免費服務仍可能被 Render 自動重新啟動；免費服務沒有持久磁碟，因此本專案不把資料庫存放在 Render 本機。
- Render 免費方案的每月免費執行時數有限，超過額度時服務可能暫停。
- 因此免費託管適合測試與小型興趣專案，不應視為有 SLA 的永久 24/7 服務。

### Supabase 免費方案的重要限制

- 免費專案有資料庫容量與使用限制。
- 如果一週內資料庫活動太少，Supabase 可能暫停 Free 專案；恢復後資料仍保留，但 Bot 在資料庫恢復前不能讀寫設定。
- 請不要把資料庫密碼公開，也不要把完整連線字串提交到 GitHub。

## 三、建立 Supabase 資料庫

1. 前往 [Supabase](https://supabase.com/)，建立專案。
2. 建立時設定資料庫密碼，自己妥善保存。
3. 進入專案，按頁面上方 **Connect**。
4. 選擇 **Session pooler**，複製 PostgreSQL URI。Render 常見的 IPv4 網路環境可使用 Supabase Shared Pooler 的 Session mode。
5. 把連線字串中的 `[YOUR-PASSWORD]` 換成你的資料庫密碼。如果密碼含 `#`、`?`、`&`、空格等保留字元，必須先做 URL percent-encoding。
6. 程式第一次啟動時會自動建立 `guild_settings` 與 `ai_history` 資料表，不需要手動貼 SQL。

**建議使用 Session pooler 的連線字串（通常為 5432 埠），不要自行拼接主機名稱或帳號；請直接複製 Supabase Dashboard 顯示的字串。**

## 四、設定 AI API

本專案預設使用 Groq 的 OpenAI 相容 API：

1. 前往 [Groq Console](https://console.groq.com/)，建立 API Key。
2. Render 環境變數填入：

```env
AI_API_KEY=你的_API_Key
AI_BASE_URL=https://api.groq.com/openai/v1
AI_MODEL=openai/gpt-oss-120b
AI_MAX_TOKENS=700
AI_HISTORY_LIMIT=12
```

如改用其他 OpenAI 相容供應商，請依該供應商文件調整 `AI_BASE_URL`、`AI_MODEL` 與 `AI_API_KEY`。免費額度、速率限制及模型名稱可能變動；Bot 的 AI 功能需要有效 API Key。

## 五、上傳到 GitHub

1. 解壓縮本 ZIP。
2. 在 GitHub 建立新的 **Private repository**，例如 `sammy-discord-bot`。
3. 把解壓縮後的所有檔案與 `cogs`、`tests` 資料夾上傳，保留原本的資料夾結構。
4. **不要上傳 `.env`、Discord Token、AI API Key 或資料庫密碼。** `.env.example` 可以上傳，裡面沒有真實密鑰。

## 六、部署到 Render

1. 在 [Render](https://render.com/) 建立帳號並連結 GitHub。
2. 選擇 **New → Blueprint**，選擇你的 `sammy-discord-bot` repository。
3. Render 會讀取 `render.yaml`，建立 Free Web Service。
4. 在服務的 **Environment** 頁面設定下列三個必要環境變數：

| Key | Value |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord Developer Portal 的 Bot Token |
| `DATABASE_URL` | Supabase 的 Session pooler URI |
| `AI_API_KEY` | Groq 或其他供應商的 API Key |

5. 其他環境變數已在 `render.yaml` 設定，通常不用更改。
6. 儲存後 Deploy。查看 Render Logs，成功時會看到資料庫初始化、extension 載入、Slash Commands 同步及 `Logged in as ...` 訊息。
7. 開啟 Render 提供的網址加上 `/healthz`，應會看到 JSON 狀態。`discord_connected` 在 Discord Gateway 尚未登入或正在重連時可能暫時是 `false`。

## 七、本機測試（Windows）

需要 Python 3.11 或更新版本。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

編輯 `.env`，至少填入 `DISCORD_BOT_TOKEN`。如要測試 AI，再填 `AI_API_KEY`。若 `DATABASE_URL` 留白，就會在 `data/bot.sqlite3` 建立 SQLite 資料庫。

```powershell
python main.py
```

停止本機 Bot：在終端機按 `Ctrl + C`。

## 八、Discord 指令設定

所有設定指令都需要 **Manage Server** 權限，並且必須在伺服器內使用。

### Welcome

1. `/welcome configure`：打開表單，輸入歡迎頻道 ID 或頻道提及，以及歡迎訊息模板。
2. `/welcome enable enabled:true`：啟用；設 `false` 可停用。
3. `/welcome image`：用 Discord 表單輸入或清除公開 HTTPS 背景圖片 URL。
4. `/welcome image-toggle enabled:true`：啟用歡迎圖片；設 `false` 可停用圖片。
5. `/welcome avatar-position position:Center`：設定頭像位置，可選 Left、Center、Right。
6. `/welcome test`：預覽目前訊息和圖片。

模板變數：

| 變數 | 顯示內容 |
|---|---|
| `<id>` | 新成員的顯示名稱 |
| `%guild%` | 伺服器名稱 |
| `%user%` | 新成員的 Discord 提及 |
| `%member_count%` | 目前伺服器成員人數 |
| `<#CHANNEL_ID>` | Discord 頻道提及，例如 `<#123456789012345678>`，會由 Discord 自動顯示成頻道連結 |

背景圖片必須是可公開讀取的直接 HTTPS 圖片連結，不可是登入後才看得到的網頁。下載大小上限 8 MB；圖片輸出為 1100 × 500，頭像為圓形並帶珊瑚紅色外框。

### AI Auto-reply

1. `/ai set-channel`：選擇 AI 自動回覆頻道。
2. `/ai prompt`：設定 AI 的個性、角色、語氣與回答規則。
3. AI 會回覆該頻道中的非 Bot 文字訊息；其他頻道不會觸發。
4. `/ai status`：查看 AI 頻道、API 設定狀態與模型。
5. `/ai clear-history`：清除目前頻道儲存的對話紀錄。
6. `/ai disable`：停用本伺服器的 AI 自動回覆。

AI 對話紀錄會按伺服器與頻道分開保存，並限制保留數量。能在 AI 頻道發言的人，其訊息文字可能會傳送到你設定的 AI API 供應商；不要在該頻道貼上密碼、個資或其他敏感內容。

### Auto Role

1. `/autorole set`：選擇新成員加入時要給予的身分組。
2. `/autorole enable enabled:true`：啟用；設 `false` 可停用。
3. `/autorole status`：查看目前設定。
4. `/autorole clear`：清除身分組設定。

Bot 必須具有 **Manage Roles** 權限，而且 Bot 的最高身分組必須高於要授予的身分組。@everyone、整合管理的身分組及高於 Bot 的身分組不能授予。

## 指令總表

| 指令 | 用途 |
|---|---|
| `/welcome configure` | 設定歡迎頻道與模板 |
| `/welcome enable` | 啟用／停用歡迎訊息 |
| `/welcome image` | 設定／清除背景圖片 URL |
| `/welcome image-toggle` | 啟用／停用歡迎圖片 |
| `/welcome avatar-position` | 設定頭像左／中／右位置 |
| `/welcome test` | 預覽歡迎訊息 |
| `/ai set-channel` | 設定 AI 頻道 |
| `/ai disable` | 停用 AI 回覆 |
| `/ai prompt` | 設定 AI 人格提示詞 |
| `/ai status` | 查看 AI 設定 |
| `/ai clear-history` | 清除目前頻道 AI 歷史 |
| `/autorole set` | 選擇自動身分組 |
| `/autorole enable` | 啟用／停用自動身分組 |
| `/autorole clear` | 清除自動身分組設定 |
| `/autorole status` | 查看自動身分組設定 |

## 九、測試

```powershell
python -m unittest discover -s tests -v
```

## 常見問題

- **歡迎訊息沒有出現**：確認 Server Members Intent 已開啟，並確認 `/welcome enable enabled:true`、頻道設定與 Bot 的 View Channel / Send Messages 權限。
- **AI 沒有回覆**：確認 Message Content Intent 已開啟、`AI_API_KEY` 正確、模型名稱有效、AI 頻道已設定，並查看 Render Logs 是否有 API 額度或速率限制錯誤。
- **自動身分組失敗**：確認 Bot 有 Manage Roles，且 Bot 最高身分組高於指定身分組。
- **圖片無法產生**：改用公開可讀取的直接 HTTPS 圖片連結；部分圖床會阻擋程式下載。
- **Slash Commands 沒出現**：確認邀請連結有 `applications.commands`。全域指令同步可能需要時間；開發測試時可以暫時設定 `COMMAND_GUILD_ID` 為測試伺服器 ID，之後移除即可。
- **Supabase 暫停**：到 Supabase Dashboard 查看專案狀態並依照畫面提示恢復。Bot 在資料庫恢復前無法正常讀取伺服器設定。

## 安全提醒

- 不要公開 `.env`、Bot Token、AI API Key 或 `DATABASE_URL`。
- Bot 不會要求使用者在 Discord 指令中輸入密鑰。
- AI 頻道的內容會送到外部 AI API；請向伺服器成員說明這件事。
- 本專案為興趣用途，免費主機和免費 API 都不保證永久可用或 24/7 服務。
