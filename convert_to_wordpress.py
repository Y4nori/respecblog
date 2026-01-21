#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旧サイトのHTMLファイルをWordPress用のWXRインポートファイルに変換するスクリプト
"""

import os
import re
import json
from datetime import datetime
from html.parser import HTMLParser
from xml.sax.saxutils import escape
import urllib.parse

class BlogHTMLParser(HTMLParser):
    """ブログ記事のHTMLを解析するパーサー"""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.date = ""
        self.date_published = ""
        self.date_modified = ""
        self.content_sections = []
        self.tags = []
        self.images = []

        # パース状態管理
        self.in_entry_header = False
        self.in_heading_h2 = False
        self.in_release_date = False
        self.in_entry_body = False
        self.in_json_ld = False
        self.in_related_tags = False
        self.in_tag_link = False
        self.title_found = False  # タイトルが見つかったかどうか

        self.current_section = None
        self.current_text = ""
        self.depth = 0
        self.entry_body_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # JSON-LD スクリプト検出
        if tag == "script" and attrs_dict.get("type") == "application/ld+json":
            self.in_json_ld = True
            self.current_text = ""

        # タイトルのh2検出（entry_header内のもののみ、最初の1つだけ）
        if tag == "div" and "entry_header" in attrs_dict.get("class", ""):
            self.in_entry_header = True

        if self.in_entry_header and not self.title_found and tag == "h2" and "h" in attrs_dict.get("class", ""):
            self.in_heading_h2 = True
            self.current_text = ""

        # 日付検出
        if tag == "p" and "release_date" in attrs_dict.get("class", ""):
            self.in_release_date = True
            self.current_text = ""

        # 記事本文検出
        if tag == "div" and "entry_body" in attrs_dict.get("class", ""):
            self.in_entry_body = True
            self.entry_body_depth = self.depth
            self.content_sections = []

        # 関連タグ検出
        if tag == "section" and "contents_related_tags" in attrs_dict.get("class", ""):
            self.in_related_tags = True

        if self.in_related_tags and tag == "a":
            self.in_tag_link = True
            self.current_text = ""

        # 本文内の画像検出
        if self.in_entry_body and tag == "img":
            src = attrs_dict.get("src", "")
            if src and "logo" not in src and "common/upload_data" in src:
                self.images.append(src)

        self.depth += 1

    def handle_endtag(self, tag):
        self.depth -= 1

        if tag == "script" and self.in_json_ld:
            self.in_json_ld = False
            try:
                data = json.loads(self.current_text)
                if "datePublished" in data:
                    self.date_published = data["datePublished"]
                if "dateModified" in data:
                    self.date_modified = data["dateModified"]
                # JSON-LDのheadlineからタイトルを取得
                if "headline" in data and not self.title:
                    headline = data["headline"]
                    # タイトルから余分な部分を削除
                    if "｜" in headline:
                        headline = headline.split("｜")[0].strip()
                    self.title = headline
            except:
                pass

        if tag == "h2" and self.in_heading_h2:
            self.in_heading_h2 = False
            if not self.title_found:
                self.title = self.current_text.strip()
                # タイトルから余分な部分を削除
                if "｜" in self.title:
                    self.title = self.title.split("｜")[0].strip()
                self.title_found = True

        # entry_headerを抜けたらフラグを落とす
        if tag == "div" and self.in_entry_header:
            self.in_entry_header = False

        if tag == "p" and self.in_release_date:
            self.in_release_date = False
            self.date = self.current_text.strip()

        if tag == "div" and self.in_entry_body and self.depth <= self.entry_body_depth:
            self.in_entry_body = False

        if tag == "section" and self.in_related_tags:
            self.in_related_tags = False

        if tag == "a" and self.in_tag_link:
            self.in_tag_link = False
            tag_text = self.current_text.strip()
            if tag_text.startswith("#"):
                tag_text = tag_text[1:]
            if tag_text:
                self.tags.append(tag_text)

    def handle_data(self, data):
        if self.in_json_ld:
            self.current_text += data
        elif self.in_heading_h2:
            self.current_text += data
        elif self.in_release_date:
            self.current_text += data
        elif self.in_tag_link:
            self.current_text += data


def extract_content_from_html(html_content):
    """HTMLから記事本文を抽出し、画像パスを変換する"""

    # entry_body部分を抽出
    match = re.search(r'<div class="entry_body[^"]*">(.*?)</div>\s*<div class="article-signature">',
                      html_content, re.DOTALL)
    if not match:
        # 別のパターンを試す
        match = re.search(r'<div class="entry_body[^"]*">(.*?)</div>\s*</article>',
                          html_content, re.DOTALL)

    if not match:
        return ""

    content = match.group(1)

    # 不要なformタグを削除
    content = re.sub(r'<form[^>]*>.*?</form>', '', content, flags=re.DOTALL)

    # セクションからテキストと画像を抽出して整形
    cleaned_content = ""

    # 各セクション（contents_box01やcomposite_box01）を処理
    sections = re.findall(r'<(?:section|div)[^>]*class="[^"]*(?:contents_box01|composite_box01)[^"]*"[^>]*>(.*?)</(?:section|div)>\s*(?=<(?:section|div)|$)',
                          content, re.DOTALL)

    for section in sections:
        # 見出しを抽出
        heading_match = re.search(r'<h[23] class="h">([^<]+)</h[23]>', section)
        if heading_match:
            heading_text = heading_match.group(1).strip()
            cleaned_content += f"<h3>{heading_text}</h3>\n"

        # 段落テキストを抽出
        paragraphs = re.findall(r'<p>([^<]*(?:<[^/p][^>]*>[^<]*</[^>]+>)?[^<]*)</p>', section)
        for p in paragraphs:
            p_text = p.strip()
            if p_text and p_text != "&nbsp;":
                # HTMLエンティティをデコード
                p_text = p_text.replace("&hellip;", "…")
                cleaned_content += f"<p>{p_text}</p>\n"

        # 画像を抽出して変換
        images = re.findall(r'<img[^>]*src="([^"]+)"[^>]*>', section)
        for img_src in images:
            if "logo" not in img_src and "common/upload_data" in img_src:
                # 画像パスを /imgblog/ に変換
                filename = os.path.basename(img_src)
                new_src = f"/imgblog/{filename}"
                cleaned_content += f'<img src="{new_src}" alt="" />\n'

    # セクションが見つからない場合は全体から抽出
    if not cleaned_content:
        # 段落を抽出
        paragraphs = re.findall(r'<p>([^<]+)</p>', content)
        for p in paragraphs:
            p_text = p.strip()
            if p_text and p_text != "&nbsp;":
                p_text = p_text.replace("&hellip;", "…")
                cleaned_content += f"<p>{p_text}</p>\n"

        # 画像を抽出
        images = re.findall(r'<img[^>]*src="([^"]+)"[^>]*>', content)
        for img_src in images:
            if "logo" not in img_src and "common/upload_data" in img_src:
                filename = os.path.basename(img_src)
                new_src = f"/imgblog/{filename}"
                cleaned_content += f'<img src="{new_src}" alt="" />\n'

    return cleaned_content.strip()


def parse_article(html_file_path):
    """記事HTMLファイルを解析してデータを抽出"""

    with open(html_file_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    parser = BlogHTMLParser()
    parser.feed(html_content)

    # 本文を抽出
    content = extract_content_from_html(html_content)

    # 画像パスを変換
    images = []
    for img in parser.images:
        filename = os.path.basename(img)
        images.append({
            'original': img,
            'new': f'/imgblog/{filename}',
            'filename': filename
        })

    # 日付をパース
    pub_date = parser.date_published or parser.date
    if "/" in pub_date:
        # 2022/06/14 形式
        try:
            dt = datetime.strptime(pub_date, "%Y/%m/%d")
            pub_date = dt.strftime("%Y-%m-%dT%H:%M:%S")
        except:
            pass

    return {
        'title': parser.title,
        'date': pub_date,
        'date_modified': parser.date_modified,
        'content': content,
        'tags': parser.tags,
        'images': images,
        'source_file': html_file_path
    }


def parse_gallery_items(html_file_path):
    """施工事例一覧ページから個別アイテムを抽出"""

    with open(html_file_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    items = []

    # 各ギャラリーアイテムを抽出
    # パターン: <div class="inner_item">...</div>
    item_pattern = r'<div class="inner_item">\s*<a href="[^"]*">\s*<img src="([^"]*)"[^>]*alt="([^"]*)"[^>]*>\s*</a>\s*<div class="heading[^"]*"><h3 class="h"[^>]*>([^<]*)</h3>(?:<p>([^<]*)</p>)?</div>\s*<div class="infotxt">([^<]*(?:<[^>]*>[^<]*)*)</div>\s*</div>'

    # より柔軟なパターンで抽出
    inner_items = re.findall(r'<div class="inner_item">(.*?)</div>\s*(?=<!--|\s*<div class="inner_item">|</div>\s*</div>)', html_content, re.DOTALL)

    for item_html in inner_items:
        # 画像を抽出
        img_match = re.search(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"', item_html)
        if not img_match:
            continue

        img_src = img_match.group(1)
        img_alt = img_match.group(2)

        # タイトルを抽出
        title_match = re.search(r'<h3 class="h"[^>]*>([^<]*)</h3>', item_html)
        title = title_match.group(1).strip() if title_match else img_alt

        # サブタイトル（あれば）
        subtitle_match = re.search(r'</h3>\s*<p>([^<]*)</p>', item_html)
        subtitle = subtitle_match.group(1).strip() if subtitle_match else ""

        # 説明文を抽出
        infotxt_match = re.search(r'<div class="infotxt">(.*?)</div>', item_html, re.DOTALL)
        description = ""
        if infotxt_match:
            desc_html = infotxt_match.group(1)
            # HTMLタグを保持しつつ整形
            description = desc_html.strip()

        # カテゴリを抽出（data-targetから）
        category_match = re.search(r'data-target="([^"]*)"', item_html)
        category = category_match.group(1) if category_match else "施工事例"

        # 画像パスを変換
        filename = os.path.basename(img_src)
        new_img_src = f"/imgblog/{filename}"

        # 本文を構成
        content = ""
        if subtitle:
            content += f"<p><strong>{subtitle}</strong></p>\n"
        if new_img_src:
            content += f'<img src="{new_img_src}" alt="{title}" />\n'
        if description:
            content += description

        items.append({
            'title': title,
            'date': '',  # 施工事例には日付がない
            'date_modified': '',
            'content': content,
            'tags': [category] if category else [],
            'images': [{
                'original': img_src,
                'new': new_img_src,
                'filename': filename
            }],
            'source_file': html_file_path,
            'post_type': 'sekou_jirei'  # カスタム投稿タイプ
        })

    return items


def generate_wxr_xml(articles, output_path):
    """WordPress WXR形式のXMLを生成"""

    xml_header = '''<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"
    xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
    xmlns:content="http://purl.org/rss/1.0/modules/content/"
    xmlns:wfw="http://wellformedweb.org/CommentAPI/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:wp="http://wordpress.org/export/1.2/">

<channel>
    <title>株式会社リスペック Blog</title>
    <link>https://respec-office.net</link>
    <description>大阪の防水工事は株式会社リスペック</description>
    <pubDate>''' + datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000") + '''</pubDate>
    <language>ja</language>
    <wp:wxr_version>1.2</wp:wxr_version>
    <wp:base_site_url>https://respec-office.net</wp:base_site_url>
    <wp:base_blog_url>https://respec-office.net</wp:base_blog_url>
'''

    # 全タグを収集
    all_tags = set()
    for article in articles:
        for tag in article.get('tags', []):
            all_tags.add(tag)

    # タグ定義を追加
    tag_xml = ""
    for i, tag in enumerate(sorted(all_tags), 1):
        tag_slug = urllib.parse.quote(tag, safe='')
        tag_xml += f'''
    <wp:tag>
        <wp:term_id>{i}</wp:term_id>
        <wp:tag_slug><![CDATA[{tag_slug}]]></wp:tag_slug>
        <wp:tag_name><![CDATA[{tag}]]></wp:tag_name>
    </wp:tag>
'''

    # 記事を追加
    items_xml = ""
    for i, article in enumerate(articles, 1):
        title = escape(article.get('title', ''))
        content = article.get('content', '')
        pub_date = article.get('date', '')

        # 日付をWordPress形式に変換
        try:
            if 'T' in pub_date:
                dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(pub_date, "%Y-%m-%d")
            wp_date = dt.strftime("%Y-%m-%d %H:%M:%S")
            wp_date_gmt = dt.strftime("%Y-%m-%d %H:%M:%S")
            rfc_date = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except:
            wp_date = "2022-01-01 00:00:00"
            wp_date_gmt = "2022-01-01 00:00:00"
            rfc_date = "Sat, 01 Jan 2022 00:00:00 +0000"

        # スラッグを生成（タイトルから）
        slug = urllib.parse.quote(article.get('title', f'post-{i}'), safe='')

        # タグのカテゴリーを追加
        tags_xml = ""
        for tag in article.get('tags', []):
            tag_slug = urllib.parse.quote(tag, safe='')
            tags_xml += f'''
            <category domain="post_tag" nicename="{tag_slug}"><![CDATA[{tag}]]></category>'''

        # 投稿タイプを決定
        post_type = article.get('post_type', 'post')
        if post_type == 'sekou_jirei':
            link_path = 'sekou_jirei'
        else:
            link_path = 'blog'

        items_xml += f'''
    <item>
        <title>{title}</title>
        <link>https://respec-office.net/{link_path}/{slug}/</link>
        <pubDate>{rfc_date}</pubDate>
        <dc:creator><![CDATA[admin]]></dc:creator>
        <guid isPermaLink="false">https://respec-office.net/?p={i}</guid>
        <description></description>
        <content:encoded><![CDATA[{content}]]></content:encoded>
        <excerpt:encoded><![CDATA[]]></excerpt:encoded>
        <wp:post_id>{i}</wp:post_id>
        <wp:post_date><![CDATA[{wp_date}]]></wp:post_date>
        <wp:post_date_gmt><![CDATA[{wp_date_gmt}]]></wp:post_date_gmt>
        <wp:comment_status><![CDATA[closed]]></wp:comment_status>
        <wp:ping_status><![CDATA[closed]]></wp:ping_status>
        <wp:post_name><![CDATA[{slug}]]></wp:post_name>
        <wp:status><![CDATA[publish]]></wp:status>
        <wp:post_parent>0</wp:post_parent>
        <wp:menu_order>0</wp:menu_order>
        <wp:post_type><![CDATA[{post_type}]]></wp:post_type>
        <wp:post_password><![CDATA[]]></wp:post_password>
        <wp:is_sticky>0</wp:is_sticky>{tags_xml}
    </item>
'''

    xml_footer = '''
</channel>
</rss>
'''

    full_xml = xml_header + tag_xml + items_xml + xml_footer

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(full_xml)

    return len(articles)


def collect_image_list(articles, output_path):
    """画像リストを出力（元パス→新パスの対応表）"""

    image_map = {}
    for article in articles:
        for img in article.get('images', []):
            image_map[img['original']] = img['new']

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# 画像パス変換リスト\n")
        f.write("# 元のパス -> 新しいパス (/imgblog/)\n\n")
        for original, new in sorted(image_map.items()):
            f.write(f"{original} -> {new}\n")

    return len(image_map)


def generate_csv(articles, output_path):
    """記事一覧をCSVで出力"""
    import csv

    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        # ヘッダー
        writer.writerow([
            'No',
            '種別',
            'フォルダ名',
            'タイトル',
            '日付',
            'タグ',
            '画像数',
            '本文(先頭100文字)'
        ])

        for i, article in enumerate(articles, 1):
            # フォルダ名を抽出
            folder = os.path.basename(os.path.dirname(article['source_file']))

            # 種別を判定
            post_type = article.get('post_type', 'post')
            type_label = 'ブログ' if post_type == 'post' else '施工事例'

            # 本文からHTMLタグを除去して先頭100文字
            content_text = re.sub(r'<[^>]+>', '', article.get('content', ''))
            content_preview = content_text[:100].replace('\n', ' ')

            writer.writerow([
                i,
                type_label,
                folder,
                article.get('title', ''),
                article.get('date', ''),
                ', '.join(article.get('tags', [])),
                len(article.get('images', [])),
                content_preview
            ])

    return len(articles)


def main():
    """メイン処理"""
    import sys

    base_dir = "/home/user/respecblog"
    detail_dir = os.path.join(base_dir, "detail")

    # コマンドライン引数で処理を切り替え
    mode = sys.argv[1] if len(sys.argv) > 1 else "csv"

    articles = []
    gallery_items = []

    # detail/ディレクトリ内の全記事を処理（ブログ）
    print("=== ブログ記事を処理中 ===")
    for entry in os.listdir(detail_dir):
        entry_path = os.path.join(detail_dir, entry)
        if os.path.isdir(entry_path):
            index_path = os.path.join(entry_path, "index.html")
            if os.path.exists(index_path):
                print(f"Processing: {entry}")
                try:
                    article = parse_article(index_path)
                    if article['title']:  # タイトルがある記事のみ追加
                        article['post_type'] = 'post'
                        articles.append(article)
                        print(f"  Title: {article['title']}")
                        print(f"  Date: {article['date']}")
                        print(f"  Tags: {len(article['tags'])}")
                        print(f"  Images: {len(article['images'])}")
                except Exception as e:
                    print(f"  Error: {e}")

    print(f"\nブログ記事: {len(articles)} 件")

    # 施工事例ページを処理
    print("\n=== 施工事例を処理中 ===")
    for i in range(1, 9):  # 施工事例1-8
        gallery_dir = os.path.join(base_dir, f"施工事例{i}")
        index_path = os.path.join(gallery_dir, "index.html")
        if os.path.exists(index_path):
            print(f"Processing: 施工事例{i}")
            try:
                items = parse_gallery_items(index_path)
                gallery_items.extend(items)
                print(f"  Found {len(items)} items")
            except Exception as e:
                print(f"  Error: {e}")

    print(f"\n施工事例: {len(gallery_items)} 件")

    # 全アイテムを結合
    all_items = articles + gallery_items

    # ブログ記事は日付でソート、施工事例は後ろに配置
    articles.sort(key=lambda x: x.get('date', ''), reverse=True)
    all_items = articles + gallery_items

    print(f"\n合計 {len(all_items)} 件を処理しました")

    if mode == "csv":
        # CSV出力のみ
        output_csv = os.path.join(base_dir, "articles_list.csv")
        count = generate_csv(all_items, output_csv)
        print(f"\nCSVファイルを生成しました: {output_csv}")
        print(f"  記事数: {count}")

    elif mode == "xml":
        # WordPress WXR XMLを生成
        output_xml = os.path.join(base_dir, "wordpress_import.xml")
        count = generate_wxr_xml(all_items, output_xml)
        print(f"\nWordPress XMLファイルを生成しました: {output_xml}")
        print(f"  記事数: {count}")

        # 画像リストを出力
        output_images = os.path.join(base_dir, "image_list.txt")
        img_count = collect_image_list(all_items, output_images)
        print(f"\n画像リストを生成しました: {output_images}")
        print(f"  画像数: {img_count}")

        # 画像ファイル名の一覧を出力（ダウンロード用）
        output_filenames = os.path.join(base_dir, "image_filenames.txt")
        with open(output_filenames, 'w', encoding='utf-8') as f:
            filenames = set()
            for article in all_items:
                for img in article.get('images', []):
                    filenames.add(img['filename'])
            for fn in sorted(filenames):
                f.write(f"{fn}\n")
        print(f"\n画像ファイル名リストを生成しました: {output_filenames}")
        print(f"  ユニーク画像数: {len(filenames)}")

    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python convert_to_wordpress.py [csv|xml]")


if __name__ == "__main__":
    main()
