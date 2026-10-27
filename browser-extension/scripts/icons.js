// Original code-native icon; no external asset or brand mark.
import {writeFileSync} from 'node:fs';
import {deflateSync} from 'node:zlib';
const crc = bytes => {let n=0xffffffff;for(const b of bytes){n^=b;for(let j=0;j<8;j++)n=(n>>>1)^((n&1)?0xedb88320:0);}return (n^0xffffffff)>>>0;};
function chunk(name,data){const type=Buffer.from(name),out=Buffer.alloc(data.length+12);out.writeUInt32BE(data.length);type.copy(out,4);data.copy(out,8);out.writeUInt32BE(crc(Buffer.concat([type,data])),data.length+8);return out;}
function near(x,y,ax,ay,bx,by){const q=Math.max(0,Math.min(1,((x-ax)*(bx-ax)+(y-ay)*(by-ay))/((bx-ax)**2+(by-ay)**2)));return Math.hypot(x-ax-q*(bx-ax),y-ay-q*(by-ay))<=3.5;}
const lines=[[27,64,63,33],[63,33,99,64],[35,60,35,100],[35,100,91,100],[91,100,91,60],[57,100,57,77],[57,77,72,77],[72,77,72,100]];
for(const size of [48,128]){
 const pixels=Buffer.alloc(size*(size*4+1));
 for(let y=0;y<size;y++){pixels[y*(size*4+1)]=0;for(let x=0;x<size;x++){
  const c=[0,0,0,0];
  for(let sy=0;sy<4;sy++)for(let sx=0;sx<4;sx++){
   const xx=(x+(sx+.5)/4)*128/size,yy=(y+(sy+.5)/4)*128/size;
   const rounded=Math.hypot(Math.max(25-xx,0,xx-103),Math.max(25-yy,0,yy-103))<=25;
   let p=rounded?[33,77,133,255]:[0,0,0,0];
   if(rounded&&lines.some(l=>near(xx,yy,...l)))p=[255,255,255,255];
   if(Math.hypot(xx-99,yy-29)<=14)p=[244,188,76,255];
   for(let k=0;k<4;k++)c[k]+=p[k]/16;
  }
  for(let k=0;k<4;k++)pixels[y*(size*4+1)+1+x*4+k]=Math.round(c[k]);
 }}
 const hdr=Buffer.alloc(13);hdr.writeUInt32BE(size);hdr.writeUInt32BE(size,4);hdr[8]=8;hdr[9]=6;
 writeFileSync(new URL('../icons/icon'+size+'.png',import.meta.url),Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',hdr),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]));
}
console.log('Original PNG icons generated: 48 and 128 pixels.');
