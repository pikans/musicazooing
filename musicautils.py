import os
import re

DATA_DIR = os.path.join(os.getenv("HOME"), "musicazoo_videos")

def parse_id(media_id):
	[kind, name] = media_id.split(":", 1)
	return (kind, name)

def sanitize(ytid):
	print("given", ytid)
	return re.sub("[^-a-zA-Z0-9_:]", "?", ytid)

def path_for(media_id):
	(kind, name) = parse_id(media_id)
	if kind == "yt":
		return os.path.join(DATA_DIR, sanitize(name) + ".mp4")
	if kind == "file":
		return name
	assert False

