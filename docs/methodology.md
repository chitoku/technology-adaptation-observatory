# TAO 観測仕様

## 収集から公開まで

`対象台帳 → 収集または承認済み保存入力 → 証拠抽出 → 確度付与 → 評価 → 公開用データ`

実装済みの範囲は、単発収集、ローカル入力からの証拠抽出・確度付与、HTTPS到達の評価とJSONL出力。CLIの入力はバイト列として固定する。取得条件を変えた実サイト観測は別のキャプチャとして記録する。

### スナップショット 1.0.0

入力は最大2 MiBのUTF-8 JSONオブジェクト。以下のキーをすべて要求する。HTMLは文字コード変換済みテキストとして最大1 MiBとする。単発収集を実装する際は、文字コードや欠測の扱いを収集プロファイルに明記する。 [合成入力例](../tests/fixtures/home.snapshot.json)

| フィールド | 定義 |
| --- | --- |
| `snapshot_schema_version` | `1.0.0` |
| `captured_at` | 取得時点を秒精度のUTCで記録。未収集なら判断を記録した時点。 |
| `capture_profile` | `synthetic/1` または `manual-public-http/1`。前者は架空データ、後者は承認済みの手動キャプチャ用の識別子。地域、言語、取得方法など手動キャプチャの詳細は観測台帳に記録する。 |
| `requested_url`, `final_url` | 要求URLはHTTPS、最終URLはHTTP(S)またはnull。ASCII表記、明示パス、資格情報・ポート・クエリ・フラグメントなし。観測済みの場合は最終URLが必須。 |
| `access` | `observed` は意図した公開面の2xx、`blocked` はアクセス拒否・レート制限や閲覧を妨げる同意画面、`failed` は通信失敗・予期しない応答、`not_collected` は方針や承認範囲を理由に未取得。HTTP 200の中間画面を意図した公開面と区別する判定は収集側で行う。 |
| `http_status` | 最終応答のHTTPステータス整数（100–599）またはnull。未収集では応答メタデータをnullとする。 |
| `body_kind`, `html` | `complete_html`、`truncated_html` はHTML文字列。 `other`、`unavailable` はnull。アクセスが `observed` 以外なら `unavailable`。 |

### 観測出力 1.0.0

出力は `capture` のメタデータ、元ファイルのSHA-256、抽出された証拠、確度注釈を持つ。HTML本文は出力に含めない。観測IDは `sha256:` に `出力スキーマ版:証拠ルール版:入力SHA-256` のASCIIバイト列のSHA-256を連結したもの。各レコードの `observation_id + signal_id` が証拠の参照キー。確度ポリシーの変更だけで観測IDは変わらない。JSONの整形やプロデューサーの版が変わる場合は、その版を揃えてバイト単位の再現を検証する。 [JSON Schema](../schemas/observation-v1.0.0.schema.json) · [期待出力](../tests/fixtures/home.observation.json)

### 単発収集プロファイル public-http/2

`tao capture TARGET_ID --manifest PATH --region CC --out PATH` は、利用条件の審査を運用者が記録したローカル台帳を使う。 `--region` は観測した地域を表す運用者申告の2文字コード。通信の出口地域を確認できない場合は `ZZ` を使う。言語は対象の `locale` をリクエストに使う。台帳には参照した利用条件、対象ページと転送先、運用者の承認を記録する。サイト運営者の許可については別途、確認できた範囲を記録する。以下は架空の例。

```json
{
  "target_id": "airline-en-home",
  "locale": "en-US",
  "url": "https://airline.example.com/en/",
  "robots_url": "https://airline.example.com/robots.txt",
  "terms_url": "https://airline.example.com/terms/",
  "admission": "approved",
  "review": {
    "reviewed_on": "2026-11-23",
    "scope": "single_get_and_local_projection",
    "expected_title": "Example Airline",
    "redirect_urls": []
  }
}
```

- 保留中の台帳項目は、接続なしで `not_collected` として保存する。未知または重複したID、未完成の審査記録、既存の出力先は入力エラーとして終了する。
- HTTPSのGETを1ページずつ行い、`TAOObservatory/1` とプロジェクトのURLをUser-Agentに送る。同一オリジンの `/robots.txt` を取得し、読めるルールを対象URLと各承認済み転送先に適用する。一部の行が壊れていても、読み取れるルールは適用する。404・410はファイルなしとして審査済みページの単発GETに進む。明示的な拒否、追加の取得間隔・時間帯の指定、サーバーや通信の障害では保留する。転送は同一オリジン内で最大2回とし、robots.txtの判定にはProtegoを使用する。 [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) · [Protego](https://github.com/scrapy/protego)
- 単発の対象では、審査記録の `robots_policy: "best_effort"` で、robots.txt自体が403・444の場合にも指定ページへ1回だけGETできる。この場合は転送を追わない。読める禁止ルール、429、5xx、通信失敗では停止する。観測にポリシーと応答コードを残す。
- ソケットのタイムアウトと本文読み取りの予算は10秒。上限はrobots.txtが512 KiB、HTMLが1 MiB。 `Content-Encoding: identity` の応答を処理し、UTF-8と許容したHTTP宣言の文字コードで読み取る。再試行は次回の運用判断に委ねる。
- 明示的な `head` 内の閉じられた `title` が1件あり、その内容が審査済みの文字列と一致した場合に採用する。同意画面などを除外するための確認であり、同じタイトルを使う別ページは、この条件だけでは識別できない。タイトルが確認できた途中打ち切りのHTMLは、最終URLのHTTPSだけを証拠にする。

単発収集はスナップショット1.2.0と観測出力1.2.0を使用する。公開面の種別とStatic HTMLの技術判定を記録する。 [技術判定](technology-stack.md) `capture_context` は台帳版、対象ID、地域、言語、実行環境、User-Agent、Protego版、審査日、robots.txtのHTTPステータス、取得結果の理由を記録する。 `html` は対象ページの宣言の有無を再生するためにTAOが生成した最小HTML。元レスポンスはメモリ内で処理し、保存する文字列はソースに含まれた内容と独立した固定要素に限る。ハッシュはこの保存ファイルを参照する。後から別の宣言を調べるには、新しいプロファイルでの収集が必要になる。証拠ルール・確度ポリシーは1.0.0を使い、従来の再生結果と観測IDを維持する。 [観測JSON Schema 1.2](../schemas/observation-v1.2.0.schema.json)

### 証拠ルール 1.0.0

| シグナル | 観測条件 |
| --- | --- |
| `transport.landed_https` | 意図した公開面の最終URLがHTTPSなら `present`、HTTPなら `absent`。接続設定全体の評価は対象外。 |
| `html.root_lang_declared` | 完全なHTMLソースに明示された最初の `html` 開始タグの `lang` が空白以外を含むか。タグ省略は `unknown`。値の正当性や実際の表示言語は別の検証項目。 |
| `html.viewport_device_width` | 完全なHTMLソースの明示的な `head` 内で、`meta[name=viewport]` に `width=device-width` 宣言が見えるか。 `head` 省略は `unknown`。ソース中の宣言を対象とし、複数指定の実効値やレスポンシブ表示は別の検証項目。 |

証拠行は `signal_id, locator, state, reason` を持つ。 `absent` は前提を満たすスナップショットで当該宣言や事実を確認できなかった状態。 `unknown` は判定材料がない状態。中間ページ、非HTML、切れた本文、通信エラーは、相当する理由を記録する。ロケーターの `/html#…` は入力フィールドと見た箇所の表記であり、行番号やブラウザDOMのセレクターは提供しない。

### 確度ポリシー 1.0.0

`direct` は対象スナップショットの該当項目から直接決められる記述、`unassessed` は材料不足。 `direct` の範囲は指定ページ・時点・証拠ルール。出自の真正性、全社展開、使いやすさ、経営効果はそれぞれ別の根拠を要する。将来、間接的な痕跡や公開資料を使う場合は `indirect`、明示した独立根拠が一致する場合は `corroborated`、矛盾を保持する場合は `conflicting` を候補とし、新しい確度ポリシーで定義する。

### 評価基準 0.1.0

評価は別レコードで `observation_id, rubric_version, confidence_policy_version, criterion_id, result, evidence_refs, rationale` を持つ。技術の新しさや導入件数を点数化する項目は設けない。比較は各用途で必要な具体的条件に沿って行う。

| 条件ID | 適用条件 | 判定 |
| --- | --- | --- |
| `public_home.https_landing` | 用途が確認された公開消費者ホーム | 対応証拠が `direct/present` なら `met`、`direct/absent` なら `unmet`、欠測や版非対応は `unknown`。用途が異なると確認できた場合は `not_applicable`。 |

`tao evaluate OBSERVATION --rubric-version 0.1.0` はスナップショットから再生した観測JSONを読む。単発収集プロファイルが記録した対象は公開ホームとして判定する。手動・合成入力は `--surface consumer_homepage` で用途を指定できる。用途が未指定の場合は `unknown`、 `--surface other` は `not_applicable` とする。対象基準に対応しない証拠・確度の版は理由付きの `unknown` とする。CLIに未知の評価基準版を指定した場合は入力エラーを返す。

評価IDは評価スキーマ版、観測ID、評価基準版、確度版、条件ID、用途で決まる。元の観測は保存したまま、別の評価版や確度版による結果を持てる。評価行には根拠の `observation_id` と `signal_id` を残す。 `lang` とviewportは情報表示に使い、アクセシビリティやモバイル品質は別の検証項目とする。

### JSONL出力 0.1.0

`tao export --manifest PATH --rubric-version 0.1.0 OBSERVATION...` は、台帳の順に1対象1行の `target_result` を出し、最後に `summary` を出す。対象IDと要求URLで照合し、運用者が比較用に選んだ1件を各対象に入力する。対象に入力がなければ `no_observation`、取得に失敗していればHTTP応答と取得理由を残す。異なる時点の複数観測を比較する場合は、時点ごとに対象と入力を選んで書き出す。

集計は台帳の対象数、`access=observed` の有効観測数、欠測数と理由、4つの判定の件数を持つ。出典は対象URL、観測日時、取得プロファイル、観測ID、取得ファイルのSHA-256と各版でたどれる。出力先は標準出力。公開には別途データ掲載範囲の確認が必要となる。CrUXのようなオリジン単位の集計値は独自の出典・測定期間を持つ観測として扱う。

### 版と比較

| 改訂軸 | 変更時の扱い |
| --- | --- |
| 入出力スキーマ | フィールドや互換性の変更を管理し、破壊的変更ではメジャー版を上げる。旧版は新しい版とは別に保存する。 |
| 証拠ルール | 抽出の意味が変わる場合に上げる。同一キャプチャの再抽出で影響を確認し、旧ルールの結果を保持する。 |
| 確度ポリシー | 証拠に対する注釈の判定を変更するときに上げる。注釈は観測IDとポリシー版で識別する。 |
| 評価基準 | 適用要件・閾値・根拠の要件の変更時に上げる。既存の証拠を再利用し、旧評価を保持する。 |
| 収集プロファイル・対象台帳 | 取得手順、地域、取得方法、対象ページの変更を記録する。ページの役割が変わる場合は新しい対象IDを割り当てる。 |

時系列の差分は、まず同じプロファイル・ルールと照合する。比較できる有効観測が連続したとき、変更時期は最後の旧状態から最初の新状態までの区間で示す。欠測や版の差による見え方の変化は取得状況に反映する。
