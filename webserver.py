import cherrypy
import random
import time
import os
import redis
import json
import uuid
import subprocess
from musicautils import *
import codecs

redis = redis.Redis()

reboot_ok = (os.getenv("MZ_REBOOT") == "true")
f=codecs.open("index.html", 'r')
index_html = f.read()

YOUTUBE_DL = os.path.join(os.getenv("HOME"), ".local", "bin", "youtube-dl")

def query_search(query, search=True):
	p = subprocess.Popen([YOUTUBE_DL, "--ignore-errors", "--get-id", "--", query], cwd="/tmp", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
	out, _ = p.communicate()
	results = out.strip().decode().split('\n')

	if results != ['']:
		return results

	if not search:
		return None
	try:
		return [subprocess.check_output([YOUTUBE_DL, "--no-playlist", "--get-id", "--", "ytsearch:%s" % query], cwd="/tmp").strip().decode()]
	except:
		return None

def query_search_multiple(query, n=5):
	try:
		lines = subprocess.check_output([YOUTUBE_DL, "--no-playlist", "--get-id", "--get-title", "--", "ytsearch%d:%s" % (n, query)], cwd="/tmp").strip().decode().split("\n")
		assert len(lines) % 2 == 0
		return [{"title": ai, "ytid": bi} for ai, bi in zip(lines[::2], lines[1::2])]
	except:
		return None

VOL_SCALE = 0.7

def raw_get_volume():
	try:
		elems = subprocess.check_output(["/usr/bin/amixer", "get", "Master"]).decode().split("[")
		elems = [e.split("]")[0] for e in elems]
		elems = [e for e in elems if e.endswith("%")]
		assert len(elems) in (1, 2) and elems[0][-1] == "%"
		return int(elems[0][:-1], 10)
	except:
		return None

def get_volume():
	vol = raw_get_volume()
	if vol is None:
		return None
	else:
		return min(100, int(vol / VOL_SCALE))

def set_raw_volume(volume):
	try:
		volume = min(100, max(0, volume))
		subprocess.check_call(["/usr/bin/amixer", "set", "Master", "--", "%d%%" % volume])
	except:
		pass

def set_volume(volume):
	set_raw_volume(min(100, volume * VOL_SCALE))

class Musicazoo:
	def elems(self):
		return [json.loads(ent.decode()) for ent in redis.lrange("musicaqueue", 0, -1)]

	def titles(self, for_ytids):
		mapping = {}
		for ytid in for_ytids:
			value = redis.get("musicatitle.%s" % ytid)
			mapping[ytid] = value.decode() if value else None
		return mapping

	def loaded(self, for_ytids):
		mapping = {}
		for ytid in for_ytids:
			mapping[ytid] = os.path.exists(path_for(ytid))
		return mapping

	def find(self, uuid):
		found = [ent for ent in redis.lrange("musicaqueue", 0, -1) if json.loads(ent.decode())["uuid"] == uuid]
		assert len(found) <= 1
		return found[0] if found else None

	@cherrypy.expose
	def index(self):
		elems = self.elems()
		return index_html

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def enqueue(self, youtube_id):
		youtube_ids = query_search(youtube_id) if youtube_id else None
		if not youtube_ids:
			return json.dumps({"success": False})
		for youtube_id in youtube_ids:
			redis.rpush("musicaqueue", json.dumps({"ytid": youtube_id, "uuid": str(uuid.uuid4())}))
			redis.rpush("musicaload", youtube_id)
			redis.incr("musicacommon.%s" % youtube_id)
			redis.sadd("musicacommonset", youtube_id)
			redis.set("musicatime.%s" % youtube_id, time.time())
		return {"success": True}

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def status(self):
		elems = self.elems()
		raw_status = redis.get("musicastatus")
		playback_status = json.loads(raw_status.decode()) if raw_status else {}
		playback_status["listing"] = elems
		playback_status["titles"] = self.titles(set(elem["ytid"] for elem in elems))
		playback_status["loaded"] = self.loaded(set(elem["ytid"] for elem in elems))
		playback_status["volume"] = get_volume()
    playback_status["reboot_ok"] = reboot_ok
		return playback_status

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def list(self):
		return self.status()

	@cherrypy.expose
	def delete(self, uuid):
		found = self.find(uuid)
		while found is not None:
			count = redis.lrem("musicaqueue", 0, found)
			redis.rpush("musicaudit", "removed entry for %s at %s because of deletion request" % (found, time.ctime()))
			found = self.find(uuid)

	@cherrypy.expose
	def reorder(self, uuid, dir):
		try:
			forward = int(dir) >= 0
		except ValueError:
			return "faila"
		rel = 1 if forward else -1
		with redis.pipeline() as pipe:
			while True:
				try:
					pipe.watch("musicaqueue")
					cur_queue = pipe.lrange("musicaqueue", 0, -1)
					found = [ent for ent in cur_queue if json.loads(ent.decode())["uuid"] == uuid]
					if len(found) != 1:
						return "failb"
					cur_index = cur_queue.index(found[0])
					if (cur_index == 0 and not forward) or (cur_index == len(found) - 1 and forward):
						return "failc"
					pipe.multi()
					pipe.lset("musicaqueue", cur_index, cur_queue[cur_index + rel])
					pipe.lset("musicaqueue", cur_index + rel, cur_queue[cur_index])
					pipe.execute()
					break
				except WatchError:
					continue
		return "ok"

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def search(self, q):
		return query_search_multiple(q)

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def getvolume(self):
		return get_volume()

	@cherrypy.expose
	def setvolume(self, vol):
		vol = min(get_volume() + 5, int(vol))
		try:
			set_volume(vol)
		except ValueError:
			pass

	@cherrypy.expose
	def pause(self):
		redis.publish("musicacontrol", "pause")

	@cherrypy.expose
	def reboot(self):
		if reboot_ok:
			try:
				subprocess.check_call(["/usr/bin/sudo", "/sbin/reboot"])
			except:
				pass

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def top(self):
		members = [x.decode() for x in redis.smembers("musicacommonset")]
		frequencies = map(int,redis.mget(*["musicacommon.%s" % member for member in members]))
		titles = [x.decode() if x else "%s (loading)" % member for member, x in zip(members, redis.mget(*["musicatitle.%s" % member for member in members]))]
		frequency = list(zip(members, titles, frequencies))
		frequency.sort(reverse=True, key=lambda x: x[2])
		return frequency

	@cherrypy.expose
	@cherrypy.tools.json_out()
	def random(self):
		youtube_ids = redis.srandmember("musicacommonset", 30)
		if not youtube_ids:
			return {"success": False}
		nonrecent = []
		total = 0
		for youtube_id in youtube_ids:
			youtube_id = youtube_id.decode()
			ltime = redis.get("musicatime.%s" % youtube_id)
			if ltime is None or time.time() - (float(ltime.decode()) or 0) >= 3600:
				for i in range(int(redis.get("musicacommon.%s" % youtube_id).decode()) or 1):
					nonrecent.append(youtube_id)
		if not youtube_ids:
			return {"success": False}
		youtube_id = query_search(random.choice(nonrecent), search=False) if youtube_id else None
		if not youtube_id:
			return {"success": False}
		youtube_id = youtube_id[0]
		redis.rpush("musicaqueue", json.dumps({"ytid": youtube_id, "uuid": str(uuid.uuid4())}))
		redis.rpush("musicaload", youtube_id)
		redis.set("musicatime.%s" % youtube_id, time.time())
		return {"success": True, "ytid": youtube_id}

cherrypy.config.update({'server.socket_port': 8000})

cherrypy.tree.mount(Musicazoo(), os.getenv("MZ_LOCATION") or "/",
      config={
        '/static/': {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': "static"
                  }
      }
    )


cherrypy.engine.start()
cherrypy.engine.block()
