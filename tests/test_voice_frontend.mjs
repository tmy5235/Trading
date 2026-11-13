import { test } from 'node:test'
import assert from 'node:assert/strict'
import { useVoice } from '../frontend/src/voice.js'

test('denied microphone leaves voice stopped', async () => {
  Object.defineProperty(globalThis, 'navigator', {configurable:true,value:{mediaDevices:{getUserMedia:async()=>{let e=new Error('denied');e.name='NotAllowedError';throw e}}}})
  const voice=useVoice(); voice.selected.value='synthetic'
  await voice.start()
  assert.equal(voice.active.value,false);assert.equal(voice.busy.value,false);assert.match(voice.status.value,/denied/)
})

test('failed provider closes peer and releases microphone', async () => {
  let stopped=0, closed=0
  const track={stop(){stopped++}}, mic={getTracks:()=>[track],getAudioTracks:()=>[track]}
  Object.defineProperty(globalThis, 'navigator', {configurable:true,value:{mediaDevices:{getUserMedia:async()=>mic}}})
  globalThis.Audio=class { pause(){} }
  globalThis.RTCPeerConnection=class {
    constructor(){this.iceGatheringState='complete';this.localDescription={sdp:'test-offer'}}
    addTrack(){} createDataChannel(){return {close(){}}} async createOffer(){return {type:'offer',sdp:'test-offer'}}
    async setLocalDescription(){} close(){closed++}
  }
  globalThis.fetch=async()=>({ok:false,json:async()=>({detail:'controlled provider failure'})})
  const voice=useVoice(); voice.selected.value='synthetic';await voice.start()
  assert.equal(stopped,1);assert.equal(closed,1);assert.equal(voice.active.value,false);assert.match(voice.status.value,/controlled provider failure/)
})

test('ending while microphone permission is pending releases late track', async () => {
  let release,stopped=0
  Object.defineProperty(globalThis, 'navigator', {configurable:true,value:{mediaDevices:{getUserMedia:()=>new Promise(resolve=>{release=resolve})}}})
  const voice=useVoice();voice.selected.value='synthetic';const pending=voice.start();voice.stop()
  release({getTracks:()=>[{stop(){stopped++}}]});await pending
  assert.equal(stopped,1);assert.equal(voice.active.value,false)
})
