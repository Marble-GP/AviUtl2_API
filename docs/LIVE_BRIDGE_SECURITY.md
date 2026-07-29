# AviUtl2 Live Bridge セキュリティ境界

## 目的

外部API編集ロックは、ロック中の既存オブジェクトに対するLive Bridge経由の
設定変更・移動・削除を拒否するための安全機能である。自然言語やプロンプトを
解釈せず、構造化されたプロトコル要求をSDK編集セクション内で検査する。

## 信頼境界

保証する範囲:

- `object.set_item`
- `object.set_items`
- `object.move`
- `object.delete`
- 古いsnapshot、改変したobject ID、余分な解除フィールドを含む要求

保証しない範囲:

- AviUtl2 GUIを直接操作できるエージェント
- 同一ユーザー権限でプロセス注入やメモリ改変を行うプログラム
- 別プラグインやプロジェクトファイルを直接変更するプログラム
- ロック対象とは別のレイヤーへ新規オブジェクトを作り、映像上で覆う操作
- ロック対象の読み取り。snapshotはロック中もAliasを返す

Named Pipeはリモート接続を拒否し、DACLをLocal SystemとPipe所有者に限定する。
ただし、外部連携をEnableにした後は、同じユーザーで動作する任意のローカル
プロセスがクライアントになり得る。クライアント単位の認証機能ではない。

## ロック判定

ロック状態はAviUtl2に保存可能なオブジェクト名の`🔒`マーカーで表現する。
snapshotの`api_locked`がAPI上の正式な判定結果であり、見た目だけが似た文字列は
正式なロックとは限らない。

各mutationは次の順番で一つのSDK編集コールバック内に処理される。

1. 現在のタイムラインを再取得
2. `expected_revision`を比較
3. object indexを現在のSDKハンドルへ解決
4. 現在のオブジェクト名からロックを再判定
5. ロックされていない場合だけ変更・移動・削除

このため、snapshot取得後のGUI操作とのTOCTOU競合は、revision不一致または
現在のロック判定で拒否される。

## 手動回帰プローブ

protocol v1で次の防御を回帰確認する。

- 単一item更新拒否
- 複数item一括更新拒否
- 同一位置へのmove拒否
- delete拒否
- `unlock=true`、`api_locked=false`等の余分なフィールドを無視して拒否
- object indexの先頭ゼロ表現でも最終的なロック判定で拒否
- 範囲外indexを`OBJECT_NOT_FOUND`で拒否
- stale revisionを`STALE_PROJECT_STATE`で拒否
- revisionとobject IDの不一致、bool型revisionを`INVALID_ARGUMENT`で拒否
- ロック対象と同一配置へのvalidate/apply/direct-createを
  `PLACEMENT_COLLISION`で拒否
- 重複JSONキー、不正UTF-8、過剰なJSONネストを`INVALID_REQUEST`で拒否
- ゼロ長、1 MiB超過、途中切断フレーム後にPipeが復帰
- Unicode不可視文字を足したメソッド名を`METHOD_NOT_FOUND`で拒否
- 全プローブ後のrevision、名前、Alias、配置、ロック状態が試験前と完全一致

再現用:

```powershell
$env:PYTHONPATH = "src"
python tests/manual/live_bridge_lock_security_probe.py --pid <PID> --delete-probe
```

## 残存リスクと強化候補

1. object IDのindexは`01`のような先頭ゼロ表現も同じindexとして受理する。
   ロック回避にはならないが、正規表現を一意にするため拒否する余地がある。
2. 人がオブジェクト名を南京錠に似せた場合、見た目と`api_locked`が一致しない
   可能性がある。`🔒`で始まる全名称をfail-closedで扱う強化が可能。
3. object lockは他レイヤーへの新規作成を止めない。完成映像全体を保護する用途には
   scene lock、layer lock、または「新規作成も拒否」モードが必要。
4. revisionは53-bit内容指紋であり暗号学的認証子ではない。ただし、現在のロックは
   revision一致後にも対象オブジェクト上で再判定される。
5. 単一クライアントがPipe接続を保持すると、他クライアントを待たせるDoSは可能。
   ロック済みオブジェクトの編集権限を得ることはできない。
6. GUI自動操作権限をAIへ与えると、AI自身が解除メニューを操作できる。
   APIロックを強制境界として使う場合、エージェントからGUI操作権限を分離する。
