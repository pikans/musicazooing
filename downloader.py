import redis
import re
import traceback
import json
import os
import subprocess

from musicautils import *

YOUTUBE_DL = os.path.join(os.getenv("HOME"), ".local/bin/youtube-dl")

if not os.path.isdir(DATA_DIR):
	os.mkdir(DATA_DIR)

redis = redis.Redis()

# refresh the loading queue

while redis.lpop("musicaload") is not None:
	pass

for ent in redis.lrange("musicaqueue", 0, -1):
	redis.rpush("musicaload", json.loads(ent.decode())["ytid"])

def gen_yt_cmdline(ytid, for_title=False):
	return [YOUTUBE_DL, "--no-playlist", "--id", "--no-progress", "--format", "mp4"] + (["--get-title"] if for_title else []) + ["--", sanitize(ytid)]

def get_yt_title(ytid):
	return subprocess.check_output(gen_cmdline(ytid, for_title=True))

# "mplayer -fs"

while True:
	_, to_load = redis.blpop("musicaload")
	try:
		media_id = to_load.decode()
		(kind, name) = parse_id(media_id)
		if kind != "yt":
			continue
		ytid = name
		if redis.get("musicatitle." + media_id) is None:
			redis.set("musicatitle." + media_id, get_yt_title(ytid).strip())
		if not os.path.exists(path_for(media_id)):
			if subprocess.call(gen_yt_cmdline(ytid), cwd=DATA_DIR) != 0:
				redis.set("musicatitle." + media_id, b"Could not load video %s" % (to_load.encode(),))
				continue
			subprocess.check_call(gen_yt_cmdline(ytid), cwd=DATA_DIR)
			assert os.path.exists(path_for(media_id))
	except:
		print("Failed to load.")
		traceback.print_exc()
