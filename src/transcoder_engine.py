# Transmageddon
# Copyright (C) 2009-2011 Christian Schaller <uraeus@gnome.org>
# 
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Library General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This librarmy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Library General Public License for more details.
#
# You should have received a copy of the GNU Library General Public
# License along with this library; if not, see <http://www.gnu.org/licenses/>.

import sys
import os
import codecfinder
import presets
from gi.repository import GObject
# GObject.threads_init()
from gi.repository import GLib
from gi.repository import Gst
Gst.init(None)
from gi.repository import GstPbutils

class Transcoder(GObject.GObject):

   __gsignals__ = {
            'ready-for-querying' : (GObject.SignalFlags.RUN_LAST, None, []),
            'got-eos' : (GObject.SignalFlags.RUN_LAST, None, []),
            'missing-plugin' : (GObject.SignalFlags.RUN_LAST, None, []),
            'got-error' : (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,))
                    }

   def __init__(self, STREAMDATA, AUDIODATA, VIDEODATA):
       GObject.GObject.__init__(self)

       # Choose plugin based on Container name
       self.audiodata = AUDIODATA
       self.videodata = VIDEODATA
       self.streamdata = STREAMDATA

       # set preset directory
       Gst.preset_set_app_dir("/usr/share/transmageddon/presets/")

       # Choose plugin based on Codec Name
       # or switch to remuxing mode if any of the values are set to 'pastr'
       self.stoptoggle=False

       self.doaudio= False
       self.preset = self.streamdata['devicename']
       self.blackborderflag = False
       self.multipass = self.streamdata['multipass']
       self.passcounter = self.streamdata['passcounter']
       self.missingplugin= False
       self.probestreamid = False
       self.sinkpad = False
       self.usedstreamids = []

       # switching width and height around for rotationchoices where it makes sense
       if int(self.videodata[0]['rotationvalue']) == 1 or int(self.videodata[0]['rotationvalue']) == 3:
           nwidth = self.videodata[0]['videoheight']
           nheight = self.videodata[0]['videowidth']
           self.videodata[0]['videoheight'] = nheight
           self.videodata[0]['videowidth'] = nwidth 

       # if needed create a variable to store the filename of the multipass \
       # statistics file
       if self.multipass != 0:
           videoencoderplugin = codecfinder.get_video_encoder_element(self.videodata[0]['outputvideocaps'])
           videoencoder = Gst.ElementFactory.make(videoencoderplugin,"videoencoder")
           properties=videoencoder.get_property_names()
           if "multipass-cache-file" in properties:
               self.cachefile = (str (GLib.get_user_cache_dir()) + "/" + \
                   "multipass-cache-file" + self.streamdata['timestamp'] + ".log")
           else:
               self.multipass=0


       # gather preset data if relevant
       if self.preset != "nopreset":
           self.provide_presets()
 
       # Create transcoding pipeline
       self.pipeline = Gst.Pipeline()
       self.pipeline.set_state(Gst.State.PAUSED)

       # first check if we have a container format, if not set up output 
       # for possible outputs should not be hardcoded

       audiopreset=None
       videopreset=None
       #if self.preset != "nopreset": 
           # print "got preset and will use Quality Normal"
           # these values should not be hardcoded, but gotten from profile XML file
       #    audiopreset=None
       #    videopreset=None

       if self.streamdata['container'] == False:
          x=0
          while x < len(self.audiodata):
               if self.audiodata[x]['outputaudiocaps'] != 'noaud':
                   if not (self.audiodata[x]['outputaudiocaps'].intersect(Gst.caps_from_string("audio/mpeg, mpegversion=1, layer=3"))).is_empty():   
                       self.streamdata['container']=Gst.caps_from_string("application/x-id3")
               x=x+1
       if not self.streamdata['container']==False: 
           self.encodebinprofile = GstPbutils.EncodingContainerProfile.new("containerformat", None , self.streamdata['container'], None)

       # What to do if we are not doing video passthrough (we only support video inside a 
       # container format
           if self.videodata[0]['outputvideocaps'] !=False:
               if (self.videodata[0]['dopassthrough']==False and self.passcounter == int(0)):
                   self.videoflipper = Gst.ElementFactory.make('videoflip', None)
                   self.videoflipper.set_property("method", int(self.videodata[0]['rotationvalue']))
                   self.pipeline.add(self.videoflipper)

                   self.colorspaceconverter = Gst.ElementFactory.make("videoconvert", None)
                   self.pipeline.add(self.colorspaceconverter)

                   self.deinterlacer = Gst.ElementFactory.make('avdeinterlace', None)
                   self.pipeline.add(self.deinterlacer)
   
                   self.deinterlacer.link(self.colorspaceconverter)
                   self.colorspaceconverter.link(self.videoflipper)
                   self.deinterlacer.set_state(Gst.State.PAUSED)
                   self.colorspaceconverter.set_state(Gst.State.PAUSED)
                   self.videoflipper.set_state(Gst.State.PAUSED)
           # this part of the pipeline is used for both passthrough and re-encoding
           if (self.videodata[0]['outputvideocaps'] != False):
                   videopreset=None
                   self.videoprofile = GstPbutils.EncodingVideoProfile.new(self.videodata[0]['outputvideocaps'], videopreset, Gst.Caps.new_any(), 0)
                   self.encodebinprofile.add_profile(self.videoprofile)

       # We do not need to do anything special for passthrough for audio, since we are not
       # including any extra elements between uridecodebin and encodebin
       x=0
       while x < len(self.audiodata): 
           if self.audiodata[x]['outputaudiocaps'] != (False or "noaud"):
               if self.streamdata['container']==False:
                   self.encodebinprofile = GstPbutils.EncodingAudioProfile.new (self.audiodata[x]['outputaudiocaps'], audiopreset, Gst.Caps.new_any(), 0)
               else:
                   audiopreset=None
                   audioprofile = GstPbutils.EncodingAudioProfile.new(self.audiodata[x]['outputaudiocaps'], audiopreset, Gst.Caps.new_any(), 0)
                   audioprofile.set_name("audioprofilename"+str(x))
                   self.encodebinprofile.add_profile(audioprofile)
           x=x+1 
       
       if self.passcounter != int(0):
           passvalue = "Pass "+ str(self.passcounter)
           videoencoderplugin = codecfinder.get_video_encoder_element(self.videodata[0]['outputvideocaps'])
           self.videoencoder = Gst.ElementFactory.make(videoencoderplugin,"videoencoder")
           self.pipeline.add(self.videoencoder)
           GstPresetType = GObject.type_from_name("GstPreset")
           if GstPresetType in GObject.type_interfaces(self.videoencoder):
               self.videoencoder.load_preset(passvalue)
               properties=self.videoencoder.get_property_names()
               if "multipass-cache-file" in properties:
                   self.videoencoder.set_property("multipass-cache-file", self.cachefile)
               else:
                   self.multipass=0
           self.multipassfakesink = Gst.ElementFactory.make("fakesink", "multipassfakesink")
           self.pipeline.add(self.multipassfakesink)
           self.videoencoder.set_state(Gst.State.PAUSED)
           self.multipassfakesink.set_state(Gst.State.PAUSED)


       else:
           self.encodebin = Gst.ElementFactory.make ("encodebin", None)
           self.encodebin.connect("element-added", self.OnEncodebinElementAdd)
           self.encodebin.set_property("profile", self.encodebinprofile)
           self.encodebin.set_property("avoid-reencoding", True)
           self.pipeline.add(self.encodebin)
           self.encodebin.set_state(Gst.State.PAUSED)
       
       self.uridecoder = Gst.ElementFactory.make("uridecodebin", "uridecoder")
       self.uridecoder.set_property("uri", self.streamdata['filechoice'])
       self.uridecoder.connect('autoplug-continue', self.on_autoplug_continue)
       self.uridecoder.connect("pad-added", self.OnDynamicPad)
       self.uridecoder.connect('source-setup', self.dvdreadproperties)

       self.uridecoder.set_state(Gst.State.PAUSED)
       self.pipeline.add(self.uridecoder)
       
       if self.passcounter != int(0):
           self.videoencoder.link(self.multipassfakesink)
       else:
           self.transcodefileoutput = Gst.ElementFactory.make("filesink", \
               "transcodefileoutput")
           self.transcodefileoutput.set_property("location", \
               (self.streamdata['outputdirectory']+"/"+self.streamdata['outputfilename']))
           self.pipeline.add(self.transcodefileoutput)
           self.encodebin.link(self.transcodefileoutput)
           self.transcodefileoutput.set_state(Gst.State.PAUSED)
       self.uridecoder.set_state(Gst.State.PAUSED)

       self.BusMessages = self.BusWatcher()

       # we need to wait on this one before going further
       self.uridecoder.connect("no-more-pads", self.noMorePads)
       # print "connecting to no-more-pads"

   # Get all preset values
   def reverse_lookup(self,v):
       for k in codecfinder.codecmap:
           if codecfinder.codecmap[k] == v:
               return k

   # Gather preset values and create preset elements
   def provide_presets(self):
       devices = presets.get()
       device = devices[self.preset]
       preset = device.presets["Normal"]

       # Check for black border boolean
       border = preset.vcodec.border
       if border == "Y":
           self.blackborderflag = True
       else:
           self.blackborderflag = False
       # calculate number of channels
       chanmin, chanmax = preset.acodec.channels
       if int(self.audiodata[0]['audiochannels']) < int(chanmax):
           if int(self.audiodata[0]['audiochannels']) > int(chanmin): 
               channels = int(self.audiodata[0]['audiochannels'])
           else:
               channels = int(chanmin)
       else:
           channels = int(chanmax)
       self.audiodata[0]['outputaudiocaps']=Gst.caps_from_string(preset.acodec.name+","+"channels="+str(channels))
       # Check if rescaling is needed and calculate new video width/height keeping aspect ratio
       # Also add black borders if needed
       wmin, wmax  =  preset.vcodec.width
       hmin, hmax = preset.vcodec.height
       owidth, oheight = self.videodata[0]['videowidth'], self.videodata[0]['videoheight']

       # Get Display aspect ratio
       pixelaspectratio = preset.vcodec.aspectratio

       # Scale width / height down
       if self.videodata[0]['videowidth'] > wmax:
           self.videodata[0]['videowidth'] = wmax
           self.videodata[0]['videoheight'] = int((float(wmax) / owidth) * oheight)
       if self.videodata[0]['videoheight'] > hmax:
           self.videodata[0]['videoheight'] = hmax
           self.videodata[0]['videowidth'] = int((float(hmax) / oheight) * owidth)

       # Some encoders like x264enc are not able to handle odd height or widths
       if self.videodata[0]['videowidth'] % 2:
           self.videodata[0]['videowidth'] += 1
       if self.videodata[0]['videoheight'] % 2:
           self.videodata[0]['videoheight'] += 1

       # Add any required padding
       if self.blackborderflag == True:
           self.videodata[0]['videowidth']=wmax
           self.videodata[0]['videoheight']=hmax

       # Setup video framerate and add to caps - 
       # FIXME: Is minimum framerate really worthwhile checking for?
       # =================================================================
       rmin = preset.vcodec.rate[0].num / float(preset.vcodec.rate[0].denom)
       rmax = preset.vcodec.rate[1].num / float(preset.vcodec.rate[1].denom)
       rmaxtest = preset.vcodec.rate[1]
       orate = self.videodata[0]['videonum']/ self.videodata[0]['videodenom'] 
       if orate > rmax:
           num = preset.vcodec.rate[1].num
           denom = preset.vcodec.rate[1].denom
       elif orate < rmin:
           num = preset.vcodec.rate[0].num
           denom = preset.vcodec.rate[0].denom
       else:
           num = self.videodata[0]['videonum']
           denom = self.videodata[0]['videodenom']

       # FIXME Question - should num and denom values be updated in self.videodata?

       self.videodata[0]['outputvideocaps']=Gst.caps_from_string(preset.vcodec.name+","+"height="+str(self.videodata[0]['videoheight'])+","+"width="+str(self.videodata[0]['videowidth'])+","+"framerate="+str(num)+"/"+str(denom))

   def noMorePads(self, dbin):
       if self.passcounter == int(0):
           self.transcodefileoutput.set_state(Gst.State.PAUSED)
       GLib.idle_add(self.idlePlay)
       # print "No More pads received"

   def idlePlay(self):
        self.Pipeline("playing")
        # print "gone to playing"
        return False

   def BusWatcher(self):
     bus = self.pipeline.get_bus()
     bus.add_signal_watch()
     bus.connect('message', self.on_message)

   # this function probes for the stream id and correct pads based on it
   def padprobe(self, pad, probeinfo, userdata):
       event = probeinfo.get_event()
       eventtype=event.type
       if eventtype==Gst.EventType.STREAM_START:
           self.probestreamid = event.parse_stream_start()
           x=0
           while x < len(self.audiodata):
               if self.probestreamid==self.audiodata[x]['streamid']:
                   if self.probestreamid not in self.usedstreamids:
                       #FIXME - Need to clean usedstreamid list at some point
                       self.usedstreamids.append(self.probestreamid)
                       if self.audiodata[x]['outputaudiocaps'] != 'noaud':
                           self.sinkpad = self.encodebin.emit("request-profile-pad", "audioprofilename"+str(x))
                           pad.link(self.sinkpad)
               x=x+1
       return Gst.PadProbeReturn.OK

   def on_message(self, bus, message):
       mtype = message.type
       # print(mtype)
       if mtype == Gst.MessageType.ERROR:
           print("we got an error, life is shit")
           err, debug = message.parse_error()
           print(err) 
           print(debug)
           Gst.debug_bin_to_dot_file (self.pipeline, \
           Gst.DebugGraphDetails.ALL, 'transmageddon-debug-graph')
           #self.emit('got-error', err.message)
       elif mtype == Gst.MessageType.ELEMENT:
           if GstPbutils.is_missing_plugin_message(message):
               print("missing something")
               if self.missingplugin==False: #don't think this is correct if more than one plugin installed
                   self.missingplugin=message
                   #output=GstPbutils.missing_plugin_message_get_description(message)
                   #print(output)
                   # GstPbutils.missing_plugin_message_get_installer_detail(message)
                   self.emit('missing-plugin')
           
       elif mtype == Gst.MessageType.ASYNC_DONE:
           self.emit('ready-for-querying')
       elif mtype == Gst.MessageType.EOS:
           if (self.multipass != 0):
               if (self.passcounter == 0):
                   self.usedstreamids=[]
                   #removing multipass cache file when done
                   if os.access(self.cachefile, os.F_OK):
                       os.remove(self.cachefile)
           self.emit('got-eos')
           self.pipeline.set_state(Gst.State.NULL)
       elif mtype == Gst.MessageType.APPLICATION:
           self.pipeline.set_state(Gst.State.NULL)
           self.pipeline.remove(self.uridecoder)
       return True

   def OnDynamicPad(self, uridecodebin, src_pad):
       origin = src_pad.query_caps(None)
       print(self.streamdata['container'].to_string())
       if (self.streamdata['container']==False):
           a =  origin.to_string()
           if a.startswith("audio/"): # this is for audio only files
               sinkpad = self.encodebin.get_static_pad("audio_0")
               src_pad.link(sinkpad)
       else:
           if self.videodata[0]['outputvideocaps'] == False:
               c = origin.to_string()
               if c.startswith("audio/"):
                   sinkpad = self.encodebin.get_static_pad("audio_0")
                   print("sinkpad is " + str(sinkpad))
                   src_pad.link(sinkpad)
           else:
               # Checking if its a subtitle pad which we can't deal with
               # currently.
               # Making sure that when we remove video from a file we don't
               # bother with the video pad.
               c = origin.to_string()
               if not c.startswith("text/"):
                   if not (c.startswith("video/") and (self.videodata[0]['outputvideocaps'] == False)):
                       if self.passcounter == int(0):
                           if not c.startswith("audio/"):
                               self.sinkpad = self.encodebin.emit("request-pad", origin)
               if c.startswith("audio/"):
                   if self.passcounter == int(0):
                       src_pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self.padprobe, None)
               elif ((c.startswith("video/") or c.startswith("image/")) and (self.videodata[0]['outputvideocaps'] != False)):
                   if self.videodata[0]['dopassthrough']==False:
                       if (self.multipass != 0) and (self.passcounter != int(0)):
                           videoencoderpad = self.videoencoder.get_static_pad("sink")
                           src_pad.link(videoencoderpad)
                       else:
                           deinterlacerpad = self.deinterlacer.get_static_pad("sink")
                           src_pad.link(deinterlacerpad)
                           self.videoflipper.get_static_pad("src").link(self.sinkpad)   
                   else:
                       src_pad.link(self.sinkpad)
 
   def on_autoplug_continue(self, element, pad, caps):
        event=pad.get_sticky_event(Gst.EventType.STREAM_START, 0)
        streamid = event.parse_stream_start()
        x=0
        self.autoplugreturnvalue = True
        while x < len(self.audiodata):
           if streamid==self.audiodata[x]['streamid']:
               if self.audiodata[x]['dopassthrough'] == True:
                   self.autoplugreturnvalue = False
               elif self.audiodata[x]['outputaudiocaps']== 'noaud':
                   self.autoplugreturnvalue = False
           x=x+1
        if streamid ==self.videodata[0]['streamid']:
               if self.videodata[0]['dopassthrough'] == True:
                   self.autoplugreturnvalue = False
        capsvalue=caps.to_string()
        if capsvalue.startswith("subtitle/"): # this is to avoid wasting resources on decoding subtitles
            self.autoplugreturnvalue =False
        if self.autoplugreturnvalue == False:
            return False
        else:
            return True

   def dvdreadproperties(self, parent, element):
        if "GstDvdReadSrc" in str(element)	:
            element.set_property("device", self.streamdata['filename'])
            element.set_property("title", self.streamdata['dvdtitle'])

   def OnEncodebinElementAdd(self, encodebin, element):
       factory=element.get_factory()
       if factory != None:
           # set multipass cache file on video encoder element
           if (self.multipass != 0) and (self.passcounter == int(0)):
               if Gst.ElementFactory.list_is_type(factory, 2814749767106562): # this is the factory code for Video encoders
                   element.set_property("multipass-cache-file", self.cachefile)
           
           # Set Transmageddon as Application name using Tagsetter interface
           tagyes = factory.has_interface("GstTagSetter")
           if tagyes ==True:
               taglist=Gst.TagList.new_empty()
               taglist.add_value(Gst.TagMergeMode.APPEND, Gst.TAG_APPLICATION_NAME, "Transmageddon transcoder")
               element.merge_tags(taglist, Gst.TagMergeMode.REPLACE)
               if Gst.ElementFactory.list_is_type(factory, 1125899906842626): # Audio Encoders factory code
                   taglist=Gst.TagList.new_empty()
                   if self.audiodata[0]['languagecode'] != None:
                       taglist.add_value(Gst.TagMergeMode.APPEND, Gst.TAG_LANGUAGE_CODE, self.audiodata[0]['language'])  # FIXME: Currently only doing 1 stream
                   longname=factory.get_metadata('long-name')
                   taglist.add_value(Gst.TagMergeMode.APPEND, Gst.TAG_ENCODER, longname)
                   element.merge_tags(taglist, Gst.TagMergeMode.REPLACE)

   def Pipeline (self, state):
       if state == ("playing"):
           self.pipeline.set_state(Gst.State.PLAYING)
       elif state == ("null"):
           self.pipeline.set_state(Gst.State.NULL)
