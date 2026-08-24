# -*- coding:utf-8 -*-
import os
import io
import re
import sys
import html
import time
import json
import regex
import opencc
import random
import sqlite3
import logging
import requests
import deezer
import discogs_client
from discogs_client.exceptions import HTTPError
from PIL import Image
from typing import Tuple, Optional
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
import musicbrainzngs as mb
os.chdir(os.path.dirname(__file__))


# 读取人工核对后的种子重新上传封面
# 运行脚本需要按实际修改第 41、42、67、92、98、99、991、1023、行
# python 版本需要大于等于 3.10，运行前先在脚本目录执行 python install -r requirements.txt 安装依赖


# 基础配置
HOST = "https://dicmusic.com/ajax.php?action=torrentgroup&id="
DIC = "https://dicmusic.com/torrents.php?id="
BATCH_SIZE = 50
RANDOMSLEEPTIME = 10
ENDID = 149999
LOGPATH = "log/dic_recover.log"
UPDATECOUNT = 1
SUMMARY = 'Fix broken cover, ptpimg -> dic (kshare)'
mb.set_useragent('music', '1.0')
dz = deezer.Client()

# 原始权重定义
THRESHOLD_SEARCH = 70
THRESHOLD_URL = 1
THRESHOLD_CHK = 40
RAW_WEIGHTS = {
    "album": 45,
    "artist": 25,
    "artists": 10,
    "year": 5,
    "tracks": 15
}

# 微信消息配置
MESSID = "1000005"
PYPATH = "/root/env/bin/python3"
SENDPATH = "/root/doc/py/send.py"
SENDURL = "https://us.ieii.de/qywx/send"
ISLOCAL = True

# head
HEADDIS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'cookie': '',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0'
}
HEADITUNES = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
}
HEAD = {
    "Host": "dicmusic.com",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "Windows",
    "sec-ch-prefers-color-scheme": "dark",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36 Edg/113.0.1774.57",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Cookie": ""
}

# Discogs配置
ds = discogs_client.Client(
    'music/1.0',
    consumer_key='',
    consumer_secret=''
)

# 繁简转换
TS = opencc.OpenCC('t2s')

# SQL数据库配置
DBFILE = "./data/dic.db"
CONN = None
CURSOR = None
TABLE = "torrentGroupInfo"

# ========== 预编译SQL（只执行一次，循环内复用） ==========
# 完整数据
SQL_FULL = f"""
INSERT INTO {TABLE} (groupID, json, cover, newCover, urls)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(groupID) DO UPDATE SET
    json = excluded.json,
    cover = excluded.cover,
    newCover = excluded.newCover,
    urls = excluded.urls
"""

# 更新数据：仅groupID+其他
SQL_UPDATE = f"""
INSERT INTO {TABLE} (groupID, newCover)
VALUES (?, ?)
ON CONFLICT(groupID) DO UPDATE SET
    newCover = excluded.newCover
"""

# 简易数据：仅groupID+json
SQL_SIMPLE = f"""
INSERT INTO {TABLE} (groupID, json)
VALUES (?, ?)
ON CONFLICT(groupID) DO UPDATE SET
    json = excluded.json
"""

# 日志配置
LOG_FILE = Path(__file__).parent / LOGPATH
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# 成功匹配日志
matchLog = logging.getLogger("matched")
matchLog.setLevel(logging.INFO)
matched_handler = logging.FileHandler("./log/dic_recover_matched.log", encoding="utf-8")
matched_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
matchLog.addHandler(matched_handler)

# 匹配失败日志
missMatchLog = logging.getLogger("miss")
missMatchLog.setLevel(logging.INFO)
miss_handler = logging.FileHandler("./log/dic_recover_missed.log", encoding="utf-8")
miss_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
missMatchLog.addHandler(miss_handler)

# 需要人工确认日志
chkLog = logging.getLogger("chk")
chkLog.setLevel(logging.INFO)
chk_handler = logging.FileHandler("./log/dic_recover_chk.log", encoding="utf-8")
chk_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
chkLog.addHandler(chk_handler)

# 更新描述失败日志
updateLog = logging.getLogger("update")
updateLog.setLevel(logging.INFO)
update_handler = logging.FileHandler("./log/dic_recover_updated.log", encoding="utf-8")
update_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
updateLog.addHandler(update_handler)

# 上传图床日志
uploadLog = logging.getLogger("upload")
uploadLog.setLevel(logging.INFO)
upload_handler = logging.FileHandler("./log/dic_recover_upload.log", encoding="utf-8")
upload_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
uploadLog.addHandler(upload_handler)

# 专辑、艺术家对比日志
cmpLog = logging.getLogger("cmp")
cmpLog.setLevel(logging.INFO)
cmp_handler = logging.FileHandler("./log/dic_recover_cmp.log", encoding="utf-8")
cmp_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
cmpLog.addHandler(cmp_handler)
# 阻止传播到父logger
cmpLog.propagate = False

# 屏蔽部分日志输出
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)


# 连接数据库
def connect_sqlite(db_file):
    """连接数据库并开启性能优化参数"""
    conn = sqlite3.connect(db_file)
    # 开启WAL模式+性能参数，大幅提升写入速度
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -10000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn

# 批量写入数据库 + 批量写入日志文件
def flush_batch(conn, cursor, batch_full, batch_update, batch_simple, pip_list, ppp_str):
    """批量写入数据库 + 批量写入日志文件"""
    try:
        # 跳过空集
        if (len(batch_full) + len(batch_simple) + len(batch_update)) < 1: return False
        # 批量写入数据库
        if batch_simple:
            cursor.executemany(SQL_SIMPLE, batch_simple)
        if batch_update:
            cursor.executemany(SQL_UPDATE, batch_update)
        if batch_full:
            cursor.executemany(SQL_FULL, batch_full)
        # 批量写入日志文件
        if pip_list:
            with open('log/dic_pip.txt', 'a+', encoding='utf8') as f:
                f.write('\n'.join(pip_list) + '\n')
        if ppp_str:
            with open('log/dic_ppp.txt', 'a+', encoding='utf8') as f:
                f.write(ppp_str)
        # 统一提交事务
        conn.commit()
        # 计算本次写入首尾
        all = []
        for f in batch_full: all.append(f[0])
        for f in batch_update: all.append(f[0])
        for f in batch_simple: all.append(f[0])
        all.sort()
        logging.info(f"批量写入成功: {all[0]} - {all[-1]}")
    except Exception as e:
        conn.rollback()
        logging.error(f"批量写入失败，已回滚: {e}")
        raise

# 发送企业微信消息
def send_mess(id, mess):
    if ISLOCAL:
        os.system(f'{PYPATH} {SENDPATH} {id} "{mess}"')
    else:
        try:
            head = {'Content-Type': 'application/json'}
            jsData = {'id': id, 'message': mess}
            res = requests.post(url=SENDURL, headers=head, json=jsData)
            if res.status_code != 200:
                logging.info(f'发送消息失败：{res.content.decode("utf8")}')
        except Exception as e:
            logging.info(f'发送消息失败：{e}')
            os.system(f'{PYPATH} {SENDPATH} {id} "{mess}"')





# 遍历获取所有group数据
def get_all_group(newStartNum):
    # 初始化数据库连接
    conn = connect_sqlite(DBFILE)
    cursor = conn.cursor()
    
    # 初始化请求会话（复用连接）
    session = requests.Session()
    session.headers.update(HEAD)
    
    # 确定起始ID（只查groupID字段，比SELECT *高效）
    cursor.execute(f'SELECT groupID FROM {TABLE} ORDER BY groupID DESC LIMIT 1')
    data = cursor.fetchone()
    maxIndex = data[0] if data else 1
    if newStartNum: start_id = newStartNum
    else: start_id = maxIndex + 1
    for i in range(10): logging.info('')
    logging.info(f'数据库最大值为: 【{maxIndex}】')
    time.sleep(3)


    # 批次缓存
    batch_full = []    # 带cover的完整数据
    batch_update = []  # 带cover的更新数据
    batch_simple = []  # 简易数据（not exist/error）
    pip_list = []      # ptpimg链接日志
    ppp_str = ''       # ptpimg ID日志
    count = 0          # 当前批次累计条数

    # 读取文件获取id
    with open ('log/dic_recover_reupload.txt', 'r', encoding='utf8') as f:
        reUpList = f.readlines()

    logging.info(f'{"#"*80}')
    logging.info(f'{"#"*35} 开始运行 {"#"*35}')
    logging.info(f'{"#"*80}')

    for item in reUpList:
        try:
            coverUrl, groupId= item.split()
            groupId = int(groupId)
            # 先从数据库中提取，cover是pipimg再获取json数据判断
            if groupId <= maxIndex:
                cursor.execute(f'SELECT groupID,json,cover,newCover FROM {TABLE} WHERE groupID={groupId} LIMIT 1')
                data = cursor.fetchone()
                if not data[2] or not 'ptpimg' in data[2] or (data[3] and len(data[3]) > 10): continue
            time.sleep(1 + random.random() * 2)
            urls = None
            url = f'{HOST}{groupId}'
            res = session.get(url=url, timeout=10)
            if res.status_code == 200:
                if "DOCTYPE" in res.text[:10]:
                    # 页面不存在，加入简易批次
                    batch_simple.append((groupId, "not exist"))
                    logging.info(f'{groupId} not exist [{count}]')
                else:
                    # 正常JSON数据，提取cover
                    cover = json.loads(res.text)["response"]["group"]["wikiImage"]
                    logging.info(f'{groupId} 获取 json 数据成功 [{count}]')
                    
                    # pipimg，替换cover
                    if 'ptpimg' in cover:
                        # 替换cover
                        newCover, urls = upload_cover(groupId, json.loads(res.text), coverUrl, count)
                        if newCover:
                            update_desc(groupId, newCover)
                            pip_list.append(f'{DIC}{groupId}    {newCover}')
                        else:pip_list.append(f'{DIC}{groupId}')
                        # ppp_str += f'{groupId} '
                        if urls: urls = json.dumps(urls)
                        else: urls = None
                        batch_full.append((groupId, res.text, cover, newCover, urls))
                    else:
                        batch_full.append((groupId, res.text, cover, None, urls))
            else:
                batch_simple.append((groupId, f"status_{res.status_code}"))
                logging.error(f'{groupId} 响应 {res.status_code} - {res.text[:20]}')
                time.sleep(random.randint(10, 30))
        except Exception as e:
            batch_simple.append((groupId, "error"))
            logging.error(f'{groupId} 出错 - {e}')
            time.sleep(random.randint(10, 30))

        count += 1

        # 达到批次大小，统一刷写
        if count >= BATCH_SIZE:
            flush_batch(conn, cursor, batch_full, batch_update, batch_simple, pip_list, ppp_str)
            # 清空批次缓存
            batch_full.clear()
            batch_update.clear()
            batch_simple.clear()
            pip_list.clear()
            ppp_str = ''
            count = 0

    # 循环结束，处理最后不足一批的剩余数据
    flush_batch(conn, cursor, batch_full, batch_update, batch_simple, pip_list, ppp_str)

    # 收尾：关闭连接
    cursor.close()
    conn.close()
    session.close()
    logging.info("全部数据处理完成")





# 寻找合适的封面并上传
def upload_cover(id, jsonData, url, count):
    errList = []
    musicInfo = album_info(jsonData) # title, album, artist, year, artists, urls, desc
    # 处理json数据失败，返回none
    if musicInfo[0] == None: return (None, None)
    title, album, artist, year, artists, urls, desc = musicInfo
    logging.info(f'{"="*23} {count} {"="*23}')
    logging.info(f'开始匹配 {id} - {title} ({year})')
    # # 描述中含有链接，先用描述的链接
    # for url in urls:
    #     # # 跳过已筛选过的id
    #     # if id < 36233: continue
    alb, art, arts, yr, trk, img = get_info_from_url(url)
    # if not alb: continue
    score = calc_match_score(album, artist, ' '.join(artists), year, desc, alb, art, arts, yr, trk, id)
    logging.info(f'【{score}】: {art} - {alb} ({yr})')
    # 匹配度达标
    if score > THRESHOLD_URL:
        logging.info(f'【匹配成功】: {DIC}{id}')
        matchLog.info(f'======== {id} 【{score}】 {"="*60}')
        matchLog.info(f'{title} ({year}) - {DIC}{id}')
        matchLog.info(f'{art} - {alb} ({yr}) - {url}\n\n')
        # 上传图片
        return (upload_to_dic(id, img, f'{id}_cover.jpg'), urls)
    else:
        logging.info(f'匹配失败')
        errList.append([score, art, alb, yr, url])
    if len(errList):
        tmp = []
        logging.info(f'匹配失败')
        for data in errList:
            tmp.append(data[0])
        if min(tmp) <THRESHOLD_CHK:
            missMatchLog.info(f'========== {id} {"="*30}')
            missMatchLog.info(f'{title} ({year}) - {DIC}{id}')
        if max(tmp) >= THRESHOLD_CHK:
            chkLog.info(f'========== {id} {"="*30}')
            chkLog.info(f' {title} ({year}) - {DIC}{id}')
        for data in errList:
            if data[0] < THRESHOLD_CHK:
                missMatchLog.info(f'【{score}】 {data[2]} - {data[1]} ({data[4]}) - {url} {id}')
            else:
                chkLog.info(f'【{score}】 {data[2]} - {data[1]} ({data[4]}) - {url} {id}')
        if min(tmp) <THRESHOLD_CHK:
            missMatchLog.info(f'')
            missMatchLog.info(f'')
        if max(tmp) >= THRESHOLD_CHK:
            chkLog.info(f'')
            chkLog.info(f'')
    # 从iTunes，discogs，musicbrainz，Deezer匹配
    # return (match_cover(id, musicInfo), urls)


# 从url获取信息并返回
def get_info_from_url(url):
    url = url.lower()
    if 'musicbrainz' in url:
        if not 'release' in url: logging.info(f'url非release: {url}')
        else: return mb_release(url, True)
    elif 'apple.com' in url:
        return itunes_info_url(url, True)
    elif 'discogs' in url:
        if not '/release' in url and not '/master' in url: logging.info(f'非专辑url: {url}')
        else: return discogs_get_url(url, True)
    elif 'deezer' in url:
        if not '/album/' in url: logging.info(f'非专辑url: {url}')
        else: return deezer_get_url(url, True)
    else:
        logging.info(f'url 不在匹配库: {url}')
    return (None, None, None, None, None, None)

# 解析元数据，返回歌曲信息
def album_info(jsonData):
    try:
        # 跳过无数据
        if jsonData == 'not exist' or jsonData == 'error': return None
        # 获取元数据
        all = jsonData['response']['group']
        tor = jsonData['response']['torrents']
        # 解析数据，年份，作家等信息s
        album = html.unescape(all['name'])
        year = all['year']
        musicInfo = all['musicInfo']
        artists = musicInfo['artists']
        # 解析作曲家等
        if len(artists):
            if len(artists) > 2: artist = 'Various Artists'
            elif len(artists) > 1:artist = f'{html.unescape(artists[0]["name"])} & {html.unescape(artists[1]["name"])}'
            else: artist = artists[0]['name']
            title = f'{artist} - {album}'
            tmp = []
            for art in artists:
                tmp.append(html.unescape(art['name']))
            artists = tmp
        else:
            artist = ''
            title = album
        # 遍历追加乐手等
        for typ in musicInfo.keys():
            for art in musicInfo[typ]:
                try: artists.append(html.unescape(art['name']))
                except: logging.error(f'{all["id"]} 解析 {typ} 失败 - {art}')
        artists = list(set(artists))
        # 提取所有url链接，判断是否有音乐库链接
        urls = []
        # 从wikiBody中提取
        soup = BeautifulSoup(all['wikiBody'],'html.parser')
        for a in soup.find_all('a'):
            urls.append(a.attrs['href'])
        desc = soup.get_text()
        # 从种子描述中提取url
        for t in tor:
            for u in extract_urls(t['description']):
                urls.append(u)
        # url去重，删除非音乐库的链接
        urls = list(set(urls))
        musicUrls = ['apple.com', 'discogs.com', 'musicbrainz.org', 'bandcamp.com', 'deezer.com', 'qobuz.com', 'bugs.co', 'amazon.co', 'kkbox.com', '163.com', 'universal-music.co', 'vgmdb.net', 'vocadb.net', 'oricon.co', 'prestomusic.com', 'beatport.com', 'highresaudio.com', 'melon.com', 'metal-archives.com', 'nme.com', 'cdjapan.co', 'allmusic.com', 'soundcloud.com', 'ototoy.jp', 'pitchfork.com']
        tmp = []
        # 仅保留部分url
        for u in urls:
            for i in musicUrls:
                if i in u:
                    tmp.append(u)
                    break
        urls = tmp
        return (title, album, artist, year, artists, urls, desc)
    except Exception as e:
        all = json.loads(jsonData)['response']['group']
        logging.error(f'{all["id"]} 处理 album_info 出错 - {e}')
        return (None, None, None, None, None, None, None)

# url提取
def extract_urls(text):
    pat = r'https?://[^\s<>"\')\],。，!]+'
    raw_list = re.findall(pat, text)
    # 剔除末尾多余标点符号等
    clean = [re.sub(r'[.,。，!？)\]]+$','',u) for u in raw_list]
    clean = [re.sub(r'\[.*$','',u) for u in clean]
    return clean




# iTunes 从 url 获取歌曲信息 (album, artist, artists, year, tracks, pic)
def itunes_info_url(url, get_pic=False):
    """
    通过 Apple Music 专辑 URL 获取信息 (使用 iTunes Search API)
    注意: 此方法通过解析 URL 提取专辑 ID，然后使用 iTunes API 查找。
    并非所有 Apple Music 专辑都能在 iTunes 中找到，但大部分可以。
    """
    # 1. 从 URL 中提取专辑 ID
    parsed_url = urlparse(url)
    path_segments = parsed_url.path.split('/')
    # 专辑 ID 通常是 URL 的最后一部分
    album_id = path_segments[-1]
    if len(album_id): album_id = re.search('\d+', album_id).group()
    if not album_id.isdigit():
        raise ValueError(f"无法从 URL 中提取有效的专辑 ID: {url}")
    # 2. 使用 iTunes API 通过 ID 查找专辑
    # 使用 'lookup' 功能，id 参数为专辑 ID，entity 为 album
    try:
        # 直接使用 requests 调用 iTunes API 更灵活
        lookup_url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song"
        response = requests.get(lookup_url)
        response.raise_for_status()
        data = response.json()
        
        if data['resultCount'] == 0:
            logging.warning(f"未找到专辑")
            return (None, None, None, None, None, None)

        # 结果中第一个是专辑本身，后续是歌曲
        album_data = data['results'][0]
        songs_data = data['results'][1:]
        
        # 提取信息
        info = {
            'album': album_data.get('collectionName').replace(' - Single', ''),
            'artist': album_data.get('artistName'),
            'artists': [],
            'date': int(album_data.get('releaseDate', '')[:4]),
            'cover': album_data.get('artworkUrl100', '').replace('100x100', '1000x1000'),  # 获取更高清的封面
            'tracks': ''
        }
        for song in songs_data:
            info['tracks'] += f' {song.get("trackName")}'
            info['artists'].append(song['artistName'])
        info['artists'] = ' '.join(list(set(info['artists'])))
           
        if get_pic:
            # 获取封面
            time.sleep(random.random() * 3)
            res = requests.get(url=info['cover'], headers=HEADITUNES)
            res.raise_for_status()
            info['cover'] = res.content

        return (info['album'], info['artist'], info['artists'], info['date'], info['tracks'], info['cover'])
        
    except Exception as e:
        logging.error(f"请求 iTunes API 失败: {e} {url}")
        return (None, None, None, None, None, None)

# discogs 从 url 获取专辑信息 (album, artist, artists, year, tracks, pic)
def discogs_get_url(url, get_img=False):
    try:
        parsed_url = urlparse(url)
        path_segments = parsed_url.path.split('/')
        # 专辑 ID 通常是 URL 的最后一部分
        id = re.search('(\d+)', path_segments[-1]).group()
        if '/release' in url: data = ds.release(id)
        elif '/master' in url:
            data = ds.master(id)
            id = data.main_release.id
            time.sleep(random.random() * 3)
            data = ds.release(data.main_release.id)
        album = data.title
        artist = data.data.get('artists_sort')
        artists = ''
        for i in data.data.get("extraartists", []):
            artists += f' {i["name"]}'
        year = data.year
        tracks = ''
        for i in data.tracklist:
            tracks += f' {i.data["title"]}'
        if get_img and len(data.images):
            url = data.images[0]['uri']
            res = requests.get(url, headers=HEADDIS)
            res.raise_for_status()
            img = res.content
        elif len(data.images): img = data.images[0]['uri']
        else: img = None
        return (album, artist, artists, year, tracks, img)
    except HTTPError as e:
        if e.status_code == 404:
            logging.warning(f'Discogs {url} 无数据 {e}')
        else:
            logging.error(f"请求 Discogs 数据 {url} 失败: {e}")
        return (None, None, None, None, None, None)
    except Exception as e:
        logging.error(f"请求 Discogs 数据 {url} 失败: {e}")
        return (None, None, None, None, None, None)

# musicbrainz 从 url 获取专辑信息 (album, artist, artists, year, tracks, pic)
def mb_release(url, get_img=False):
    try:
        id = re.search('\w{8}(-\w{4}){3}-\w{12}', url).group()
        # 从 group 中获取第一个专辑 id
        if 'release-group' in url:
            res = mb.get_release_group_by_id(id, includes=["releases"])
            id = res['release-group']['release-list'][0]['id']
            time.sleep(random.random() * 3)
        # 获取专辑信息
        res = mb.get_release_by_id(id, includes=["artists", "recordings", "media"])
        album = res['release']['title']
        artist = res['release']['artist-credit'][0]['artist']['name']
        artists = []
        if 'date' in res['release']: year = res['release']['date']
        else: year = ''
        if len(year) > 5: year = int(year[:4])
        else: year = 0
        if len(res['release']['artist-credit']) and 'name' in res['release']['artist-credit'][0]:
            artist += f" ({res['release']['artist-credit'][0]['name']})"
        tracks = ''
        for i in res['release']['medium-list'][0]['track-list']:
            tracks += ' ' + i['recording']['title']
            if "artist-credit" in i:
                for credit in i["artist-credit"]:
                    if isinstance(credit, dict) and "artist" in credit:
                        artists.append(credit["artist"]["name"])
        artists = ' '.join(list(set(artists)))
        # 获取封面
        if get_img:
            if res['release']['cover-art-archive']['front'] == 'true':
                time.sleep(random.random() * 3)
                pic = mb.get_image_front(id)
            else:
                logging.warning(f'[Brainz]: 专辑无封面 - {url}')
                return (None, None, None, None, None, None)
        else: pic = id
        return (album, artist, artists, year, tracks, pic)
    except Exception as e:
        logging.error(f'[Brainz]: {url} 获取信息失败: {e}')
        return (None, None, None, None, None, None)

# deezer 从 url 获取歌曲信息 (album, artist, artists, year, tracks, pic)
def deezer_get_url(url, get_pic=False): 
    try:
        # 专辑 ID 通常是 URL 的最后一部分
        parsed_url = urlparse(url)
        path_segments = parsed_url.path.split('/')
        id = re.search('(\d+)', path_segments[-1]).group()
        # 用专辑ID获取专辑对象
        album = dz.get_album(id)
        # 提取基本信息
        title = album.title
        year = album.release_date.year
        # 专辑的主艺术家
        artist = album.get_artist().name
        # 专辑的所有贡献者（包括作曲、合唱、客串等）
        artists = ''
        if hasattr(album, 'contributors') and album.contributors:
            for contributor in album.contributors:
                artists += f" {contributor.name}"
        # 获取所有曲目名
        tracks = ''
        trs = album.get_tracks()  # 获取曲目列表[reference:4]
        for i, track in enumerate(trs, 1):
            tracks += f" {track.title}"
        # 获取高清封面图链接 (cover_xl 是最高清版本)[reference:0][reference:1]
        cover = album.cover_xl
        if len(cover) < 10: cover = album.cover_big
        if get_pic:
            res = requests.get(url=cover,headers=HEADITUNES)
            res.raise_for_status()
            pic = res.content
        else: pic = cover
        return (title, artist, artists, year, tracks, pic)
    except Exception as e:
        logging.error(f"请求 Deezer 数据 {url} 失败: {e}")
        return (None, None, None, None, None, None)






# 图片转换
def image_bin_to_jpg(
    img_bytes: bytes,
    quality: int = 85,
    max_side: int = 1000,
    max_size_kb: Optional[int] = 600
) -> Tuple[bool, Optional[bytes], Optional[str]]:
    """
    将任意图片二进制转为JPG格式二进制
    自动处理：图片宽或高大于max_side，则等比例缩小到max_side，否则不变
    :param img_bytes: 原始图片bytes
    :param quality: jpeg质量 0~100
    :param max_side: 长边最大像素限制
    :return: (success:bool, jpg_bytes|None, error_msg|None)
    """
    if not isinstance(img_bytes, bytes) or len(img_bytes) == 0:
        return False, None, "输入二进制为空或者类型错误"

    if not (0 <= quality <= 100):
        return False, None, "quality必须在0‑100之间"

    try:
        input_stream = io.BytesIO(img_bytes)
        with Image.open(input_stream) as im:
            width, height = im.size

            # 等比例缩放：长边超过max_side才处理
            if max_side != 0:
                if width > max_side or height > max_side:
                    # 计算缩放比例
                    scale = min(max_side / width, max_side / height)
                    new_w = int(round(width * scale))
                    new_h = int(round(height * scale))
                    im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # GIF动态图：Pillow仅读取第一帧
            # 处理透明通道 RGBA / P模式
            if im.mode in ("RGBA", "P"):
                # 创建白色底图
                bg = Image.new("RGB", im.size, (255, 255, 255))
                mask = im.split()[-1] if im.mode == "RGBA" else None
                bg.paste(im, mask=mask)
                out_img = bg
            elif im.mode != "RGB":
                out_img = im.convert("RGB")
            else:
                out_img = im

            # ---- 先按指定 quality 保存一次 ----
            output_buf = io.BytesIO()
            out_img.save(output_buf, format="JPEG", quality=quality, optimize=True)
            jpg_bin = output_buf.getvalue()

            # ---- 若设置了大小限制，且当前大小超标，则进行压缩 ----
            if max_size_kb is not None:
                max_bytes = max_size_kb * 1024
                if len(jpg_bin) > max_bytes:
                    logging.info(f'图片大小: {int(max_bytes / 1024)} -> {max_size_kb} kb')
                    # 首先检查最低质量（0）是否仍超标
                    output_buf = io.BytesIO()
                    out_img.save(output_buf, format="JPEG", quality=0, optimize=True)
                    min_bin = output_buf.getvalue()
                    if len(min_bin) > max_bytes:
                        # 即使质量 0 也无法满足，返回最小文件并给出警告
                        return True, min_bin, f"无法压缩到指定大小（最大 {max_size_kb} KB），当前最小为 {len(min_bin)/1024:.2f} KB，已返回最低质量图片"
                    
                    # 二分查找最大质量（0 ~ quality）使大小 <= max_bytes
                    lo, hi = 0, quality
                    best_quality = 0
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        output_buf = io.BytesIO()
                        out_img.save(output_buf, format="JPEG", quality=mid, optimize=True)
                        size = len(output_buf.getvalue())
                        if size <= max_bytes:
                            best_quality = mid
                            lo = mid + 1   # 尝试更高质量
                        else:
                            hi = mid - 1
                    # 用 best_quality 最终保存
                    output_buf = io.BytesIO()
                    out_img.save(output_buf, format="JPEG", quality=best_quality, optimize=True)
                    jpg_bin = output_buf.getvalue()
        return True, jpg_bin, None
    except Image.UnidentifiedImageError:
        return False, None, "无法识别图片，不是有效图片二进制或者图片已损坏"
    except OSError as e:
        return False, None, f"图片解码异常：{str(e)}"
    except Exception as e:
        return False, None, f"转换未知异常：{str(e)}"

# 比较数据相似度并返回得分
def calc_match_score(
        input_album: str,
        input_artist: str,
        input_artists: str,
        input_year: Optional[int],
        input_tracks: Optional[str],
        # input_total_tracks: Optional[int],
        item_album: str,
        item_artist: str,
        item_artists: str,
        item_year: Optional[int],
        item_tracks: Optional[str],
        # item_total_tracks: Optional[int]
        id: Optional[int],
) -> float:
    """
    计算单条记录的百分制匹配分数0‑100
    用户不传的输入字段，自动剔除对应权重，结果归一化
    token_sort_ratio：对译名、顺序调换有很好容错，例 "Radiohead / 电台司令"
    """
    score_components = []
    active_weight_sum = 0

    # 移除标点符号
    input_album = remove_punctuation(input_album)
    input_artist = remove_punctuation(input_artist)
    input_artists = remove_punctuation(input_artists)
    input_tracks = remove_punctuation(input_tracks)
    item_album = remove_punctuation(item_album)
    item_artist = remove_punctuation(item_artist)
    item_artists = remove_punctuation(item_artists)
    item_tracks = remove_punctuation(item_tracks)

    # 艺术家
    w = RAW_WEIGHTS["artist"]
    sim_artist = cals_multi(input_artist, item_artist, id, input_album, input_artist)
    score_components.append((sim_artist, w))
    active_weight_sum += w

    # 专辑名
    w = RAW_WEIGHTS["album"]
    sim_album = cals_multi(input_album, item_album, id, input_album, input_artist)
    score_components.append((sim_album, w))
    active_weight_sum += w

    # 其他艺术家
    w = RAW_WEIGHTS["artists"]
    if len(input_artists) and len(item_artists):
        sim_artists = fuzz.token_sort_ratio(input_artists, item_artists)
        score_components.append((sim_artists, w))
        active_weight_sum += w

    # 年份：完全相等满分，差1年50分，差距>1为0
    w = RAW_WEIGHTS["year"]
    if input_year is not None and item_year is not None:
        diff = abs(input_year - item_year)
        sim_year = 100 if diff == 0 else (50 if diff == 1 else 0)
    else:
        sim_year = 0
    score_components.append((sim_year, w))
    active_weight_sum += w

    # 歌曲：仅输入不为空才计入权重
    w = RAW_WEIGHTS["tracks"]
    if input_tracks is not None and input_tracks.strip():
        sim_first = fuzz.token_sort_ratio(input_tracks, item_tracks) if item_tracks else 0
        score_components.append((sim_first, w))
        active_weight_sum += w

    # # 总曲目5：仅输入不为空才计入权重
    # w = RAW_WEIGHTS["total_tracks"]
    # if input_total_tracks is not None:
    #     sim_tracks = 100 if item_total_tracks == input_total_tracks else 0
    #     score_components.append((sim_tracks, w))
    #     active_weight_sum += w

    raw_total = sum(sim * weight / 100.0 for sim, weight in score_components)
    if active_weight_sum <= 0:
        return 0
    final_score = raw_total / active_weight_sum * 100
    return round(final_score, 2)

# 多重国家比较匹配度
def cals_multi(org, chk, id, album, artist):
    allScore = [0, 0, 0, 0, 0]
    # 不筛选，直接比对
    allScore[0] = fuzz.token_sort_ratio(org, chk)
    type = calculate_language_ratio(org)
    cmpLog.info(f'')
    cmpLog.info(f'{"="*8} {id} {artist} - {album} {"="*8}')
    cmpLog.info(f'[all {allScore[0]}] [us: {type["us"]}, cn: {type["cn"]}, kr: {type["kr"]}]')
    # 对比单种语言
    for cty in type.keys():
        if type[cty] > 0:
            corg = keep_cty(org, cty)
            cchk = keep_cty(chk, cty)
            if len(corg) and len(cchk): score = fuzz.token_sort_ratio(corg, cchk)
            else: score = 0
            allScore[list(type.keys()).index(cty) + 1] = score
            cmpLog.info(f'[{cty} {score}]: {corg} - {cchk}')
    # cmpLog.info(f'max: {max(allScore)} - {allScore}')
    return max(allScore)

# 移除标点字符，繁体转为简体
def remove_punctuation(str):
    str = regex.sub(r'\p{P}', '', str.lower())
    return TS.convert(str)

# 仅保留指定国家的字符
def keep_cty(str, cty):
    txt = ''
    for s in str:
        code = ord(s)
        # 英文
        if cty == 'us' and ((65 <= code <= 90) or (97 <= code <= 122)):
            txt += s
        # 中文：CJK基本 4E00-9FFF，扩展A 3400-4DBF，扩展B-F（20000-2EBEF），兼容 F900-FAFF，日文：平假名 3040-309F，片假名 30A0-30FF，片假名扩展 31F0-31FF
        elif cty == 'cn' and ((0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF) or \
            (0x20000 <= code <= 0x2EBEF) or (0xF900 <= code <= 0xFAFF) or \
            (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF) or (0x31F0 <= code <= 0x31FF)):
            txt += s
        # 韩文：韩文音节 AC00-D7AF，韩文字母 1100-11FF，兼容字母 3130-318F，扩展 A960-A97F, D7B0-D7FF
        elif cty == 'kr' and ((0xAC00 <= code <= 0xD7AF) or (0x1100 <= code <= 0x11FF) or \
        (0x3130 <= code <= 0x318F) or (0xA960 <= code <= 0xA97F) or (0xD7B0 <= code <= 0xD7FF)):
            txt += s
    return txt

# 判断字符属于哪国语言
def classify_char(ch):
    """
    根据Unicode码位分类字符：
    - 'us' : 英文字母（A-Z, a-z）
    - 'cn' : 中文（CJK统一汉字及扩展），日文（平假名、片假名）
    - 'kr' : 韩文（韩文音节、字母等）
    - None : 其他（数字、空格、其他语言等）
    """
    code = ord(ch)
    # 英文
    if (65 <= code <= 90) or (97 <= code <= 122):
        return 'us'
    # 中文：CJK基本 4E00-9FFF，扩展A 3400-4DBF，扩展B-F（20000-2EBEF），兼容 F900-FAFF，日文：平假名 3040-309F，片假名 30A0-30FF，片假名扩展 31F0-31FF
    if (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF) or \
       (0x20000 <= code <= 0x2EBEF) or (0xF900 <= code <= 0xFAFF) or \
       (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF) or (0x31F0 <= code <= 0x31FF):
        return 'cn'
    # 韩文：韩文音节 AC00-D7AF，韩文字母 1100-11FF，兼容字母 3130-318F，扩展 A960-A97F, D7B0-D7FF
    if (0xAC00 <= code <= 0xD7AF) or (0x1100 <= code <= 0x11FF) or \
       (0x3130 <= code <= 0x318F) or (0xA960 <= code <= 0xA97F) or (0xD7B0 <= code <= 0xD7FF):
        return 'kr'
    # 其他字符（包括数字、空格等）忽略
    return None

# 计算文本中各语言字符的比例。返回字典 {'us': 比例, 'cn': 比例, 'kr': 比例}
def calculate_language_ratio(text):
    # # 去除标点符号
    # cleaned = remove_punctuation(text)
    # 只保留字母字符（isalpha() 对中文、韩文、日文也返回True）
    letters = [ch for ch in text if ch.isalpha()]
    total = len(letters)
    if total == 0:
        return {'us': 0, 'cn': 0, 'kr': 0}
    
    counts = {'us': 0, 'cn': 0, 'kr': 0}
    for ch in letters:
        lang = classify_char(ch)
        if lang in counts:
            counts[lang] += 1
    
    # 计算比例
    result = {k: int(v / total * 100) for k, v in counts.items()}
    return result

# 上传图片到dic图库
def upload_to_dic(id, img, imgName):
    url = 'https://dicmusic.com/upload.php?action=imgupload'
    head = {
        'Accept': 'application/json',
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36 Edg/113.0.1774.57",
        "Cookie": ""
    }
    try:
        img = image_bin_to_jpg(img)
        if img[0]: files = {"image": (imgName, img[1], "image/jpeg")}
        res = requests.post(url, files=files, headers=head)
        result = json.loads(res.content)
        if result['status'] == 'success':
            uploadLog.info(f'{id} 上传图片成功: {result["url"]}    size: {int(len(img[1]) / 1024)} kb')
            return result['url']
        else: raise ValueError(result)
    except Exception as e:
        logging.error(f'上传图片到 DIC 失败：{e}')
        return False

# 更新描述
def update_desc(id, coverUrl):
    global UPDATECOUNT
    try:
        # 获取专辑描述
        url = f'https://dicmusic.com/torrents.php?action=editgroup&groupid={id}'
        res = requests.get(url=url, headers=HEAD)
        res.raise_for_status()
        soup = BeautifulSoup(res.text,'html.parser')
        wiki = soup.select_one('#textarea_wrap_0').text.strip()
        # 获取专辑其他表单数据
        url = f'https://dicmusic.com/ajax.php?action=torrentgroup&id={id}'
        res = requests.get(url, headers=HEAD)
        info = json.loads(res.text)["response"]['group']
        # 构建post请求表单
        data = {
            'action': 'takegroupedit',
            'auth': '',
            'groupid': id,
            'image': coverUrl,
            'body': wiki,
            'releasetype': info['releaseType'],
            'summary': SUMMARY
        }
        url = 'https://dicmusic.com/torrents.php'
        res = requests.post(url=url, data=data, headers=HEAD)
        res.raise_for_status()
        time.sleep(random.random() * 3)
        # 再次请求数据，确认已更改成功
        url = f'https://dicmusic.com/ajax.php?action=torrentgroup&id={id}'
        res = requests.get(url, headers=HEAD)
        info = json.loads(res.text)["response"]['group']
        if info['wikiImage'] != coverUrl: raise ValueError(f'提交表单失败：{info["wikiImage"]} -> {coverUrl}')
        updateLog.info(f'{id} 更新成功！[{UPDATECOUNT}] - {DIC}{id}')
        UPDATECOUNT += 1
        return True
    except Exception as e:
        logging.error(f'{id} 更新失败：{e}')
        updateLog.error(f'{id} 更新失败：{e}')
        return False




if __name__ == "__main__":
    get_all_group(0)
