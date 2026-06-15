# ArnisPLATEAU

GSI＋PLATEAU高さ＋LOD2屋根形状でリアルな日本の街を生成するOSSアプリ

## 特徴
- PLATEAU APIから建物の実測高さ（measuredHeight）を自動取得
- PLATEAU LOD2 CityGMLによる屋根形状の再現
- Luanti・Bedrock・Java Edition 全3形式で出力対応
- 商用利用可能（Luanti版はMinecraftライセンス不要）
- GUIアプリ（地図でエリア選択→ワンクリック生成）

## plateau2minecraft（国交省公式）との違い
| 項目 | plateau2minecraft | ArnisPLATEAU |
|------|------|------|
| 操作 | CLIのみ | GUIワンクリック |
| 道路・地形 | なし | あり |
| データ準備 | CityGML手動DL | 自動取得 |
| 商用利用 | Minecraft前提 | Luanti版は完全可 |

## 使い方
1. ArnisPLATEAU.exe と arnis-jp.exe を同じフォルダに配置
2. ArnisPLATEAU.exe を起動
3. 地図上でエリアを選択
4. 設定でGSI・PLATEAUにチェック
5. 生成開始ボタンを押す

## ダウンロード
（Releasesページにexeを公開予定）

## クレジット・謝辞
本アプリはmusoukun氏および世界中の有志の皆様のオープンソース活動に支えられています。心より感謝申し上げます。

### 技術的参考元
- [国土地理院の建物データをArnisで取り込んでMinecraftの街をつくる](https://zenn.dev/musoukun/articles/d8c79a6b44d12c) by musoukun

### ベースソフトウェア
- [arnis-jp](https://github.com/musoukun/arnis-jp) by musoukun
- [arnis](https://github.com/louis-e/arnis) by louis-e

### 使用データ
- PLATEAU © 国土交通省（CC BY 4.0）
- 国土地理院（GSI）（CC BY 4.0）
- © OpenStreetMap contributors（ODbL）
- Luanti / Mineclonia（LGPL v2.1）

## ライセンス
Apache License 2.0
