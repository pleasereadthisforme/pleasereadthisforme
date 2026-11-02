(() => {
'use strict';
const templates = {"user": "<svg class=\"character-art\" viewBox=\"0 0 768 512\" aria-hidden=\"true\" aria-label=\"선글라스를 끼고 책상에서 한 손으로 타이핑하며 발을 까딱이는 졸라맨\"><defs><filter id=\"character-ink-user\" color-interpolation-filters=\"sRGB\" x=\"0\" y=\"0\" width=\"100%\" height=\"100%\"><feColorMatrix type=\"matrix\" values=\"0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -0.2126 -0.7152 -0.0722 1 0\"/><feComponentTransfer><feFuncA type=\"linear\" slope=\"1.0408163265\" intercept=\"-0.0408163265\"/></feComponentTransfer></filter></defs><g filter=\"url(#character-ink-user)\">\n<defs>\n <clipPath id=\"forearm-region\"><path d=\"M354 232H499V258H354Z\"/></clipPath>\n <clipPath id=\"foot-region\"><path d=\"M518 378H594V444H518Z\"/></clipPath>\n</defs>\n<!-- Leave a two-pixel sampling gap above the keyboard at y=260; erase the original arm separately. -->\n<image href=\"/activity-ui/character-user-typing-sheet-v7.png\" width=\"1536\" height=\"1024\"/>\n<path fill=\"white\" d=\"M354 232H499V260H354Z M518 378H594V444H518Z\"/>\n<g id=\"typing-arm\"><g clip-path=\"url(#forearm-region)\"><image href=\"/activity-ui/character-user-typing-sheet-v7.png\" width=\"1536\" height=\"1024\"/></g></g>\n<g id=\"tapping-foot\"><g clip-path=\"url(#foot-region)\"><image href=\"/activity-ui/character-user-typing-sheet-v7.png\" width=\"1536\" height=\"1024\"/></g></g>\n</g></svg>", "typing": "<svg class=\"character-art\" viewBox=\"0 0 768 512\" aria-hidden=\"true\"><defs><filter id=\"character-ink-typing\" color-interpolation-filters=\"sRGB\" x=\"0\" y=\"0\" width=\"100%\" height=\"100%\"><feColorMatrix type=\"matrix\" values=\"0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -0.2126 -0.7152 -0.0722 1 0\"/><feComponentTransfer><feFuncA type=\"linear\" slope=\"1.0408163265\" intercept=\"-0.0408163265\"/></feComponentTransfer></filter></defs><g filter=\"url(#character-ink-typing)\">\n\n<image href=\"/activity-ui/character-bot-typing-approved-v1.png\" width=\"768\" height=\"512\"/><path fill=\"white\" d=\"M284 232H455V293H284Z\"/>\n<g fill=\"none\" stroke=\"black\" stroke-width=\"10\" stroke-linecap=\"square\" stroke-linejoin=\"miter\" shape-rendering=\"crispEdges\"><path id=\"far-arm\"/><path id=\"near-arm\"/></g>\n</g></svg>", "thinking": "<svg class=\"character-art\" viewBox=\"0 0 1000 1000\" aria-hidden=\"true\" aria-label=\"큰 물음표에 기대어 한쪽 다리를 떠는 웃는 픽셀 졸라맨\"><defs><filter id=\"character-ink-thinking\" color-interpolation-filters=\"sRGB\" x=\"0\" y=\"0\" width=\"100%\" height=\"100%\"><feColorMatrix type=\"matrix\" values=\"0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -0.2126 -0.7152 -0.0722 1 0\"/><feComponentTransfer><feFuncA type=\"linear\" slope=\"1.0408163265\" intercept=\"-0.0408163265\"/></feComponentTransfer></filter></defs><g filter=\"url(#character-ink-thinking)\">\n<defs>\n<path id=\"leg-area\" d=\"M625 580L640 583L800 760L800 827L710 827L625 650Z\"/>\n<mask id=\"body-mask\"><rect width=\"1000\" height=\"1000\" fill=\"white\"/><use href=\"#leg-area\" fill=\"black\"/></mask>\n</defs>\n<rect width=\"1000\" height=\"1000\" fill=\"white\"/>\n<image href=\"/activity-ui/character-bot-question-v4.png\" width=\"1000\" height=\"1000\" mask=\"url(#body-mask)\"/>\n\n<!-- Hip and toe remain fixed. Knee is solved from constant limb lengths. -->\n<g class=\"leg\" fill=\"none\" stroke=\"black\" stroke-width=\"13\" stroke-linecap=\"square\" stroke-linejoin=\"round\">\n<path id=\"bent-leg\"/>\n<path id=\"foot\" fill=\"black\" stroke-width=\"4\" stroke-linejoin=\"miter\"/>\n</g>\n</g></svg>", "reading": "<svg class=\"character-art\" viewBox=\"0 0 1536 1024\" aria-hidden=\"true\" aria-label=\"책을 받치고 책장을 빠르게 넘기는 졸라맨\"><defs><filter id=\"character-ink-reading\" color-interpolation-filters=\"sRGB\" x=\"0\" y=\"0\" width=\"100%\" height=\"100%\"><feColorMatrix type=\"matrix\" values=\"0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -0.2126 -0.7152 -0.0722 1 0\"/><feComponentTransfer><feFuncA type=\"linear\" slope=\"1.0408163265\" intercept=\"-0.0408163265\"/></feComponentTransfer></filter></defs><g filter=\"url(#character-ink-reading)\">\n<image href=\"/activity-ui/character-bot-papers-v5.png\" width=\"1536\" height=\"1024\"/>\n\n<!-- Replace only the working region with articulated drawing layers. -->\n<path fill=\"white\" d=\"M561 390H650V370H738V408L893 435L963 356L993 365L1025 540L849 570L700 592L565 467Z\"/>\n<g shape-rendering=\"crispEdges\" stroke=\"black\" stroke-width=\"10\" stroke-linejoin=\"round\" fill=\"white\">\n<path d=\"M460 490L640 450L940 485L975 565L735 590Z\"/>\n<path d=\"M640 450L735 570\" fill=\"none\"/>\n<path d=\"M605 465L803 499M625 485L843 525M649 511L863 549\" fill=\"none\" stroke-width=\"5\"/>\n<path id=\"page-trail\" stroke-width=\"5\"/>\n<path id=\"page\"/>\n</g>\n<g shape-rendering=\"crispEdges\" fill=\"none\" stroke=\"black\" stroke-width=\"18\" stroke-linecap=\"square\" stroke-linejoin=\"round\">\n<path id=\"support-arm\" d=\"M991 374L1011 528L879 555L855 555\"/><path id=\"turning-arm\"/>\n</g>\n</g></svg>", "idle": "<svg class=\"character-art\" viewBox=\"0 0 1000 1000\" aria-hidden=\"true\" aria-label=\"양반다리로 앉아 발목을 잡고 몸 전체가 좌우로 기우뚱하는 졸라맨\"><defs><filter id=\"character-ink-idle\" color-interpolation-filters=\"sRGB\" x=\"0\" y=\"0\" width=\"100%\" height=\"100%\"><feColorMatrix type=\"matrix\" values=\"0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  -0.2126 -0.7152 -0.0722 1 0\"/><feComponentTransfer><feFuncA type=\"linear\" slope=\"1.0408163265\" intercept=\"-0.0408163265\"/></feComponentTransfer></filter></defs><g filter=\"url(#character-ink-idle)\">\n<rect width=\"1000\" height=\"1000\" fill=\"white\"/>\n<g id=\"seated-body\"><image href=\"/activity-ui/character-bot-idle-v8.png\" width=\"1000\" height=\"1000\"/></g>\n</g></svg>"};
const terminal=document.getElementById('terminal'), input=document.getElementById('question');
const user=document.createElement('div'); user.className='user-character'; user.setAttribute('aria-hidden','true'); user.innerHTML=templates.user;
// Crop only the SVG viewport, preserving the approved artwork and motion coordinates.
user.querySelector('svg').setAttribute('viewBox','110 75 555 395');
document.getElementById('chat').before(user);
const bot=document.createElement('div'); bot.className='bot-character'; bot.setAttribute('aria-hidden','true');
terminal.querySelector('.terminal-body').append(bot);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const userArm=user.querySelector('#typing-arm'), userFoot=user.querySelector('#tapping-foot');
const transforms=new WeakMap();
function transform(node,value){
 // Reassigning an unchanged SVG transform still invalidates its raster layers.
 if(transforms.get(node)!==value){node.setAttribute('transform',value);transforms.set(node,value);}
}
const $=s=>bot.querySelector(s);
let draw=()=>{}, state='', wanted='idle', changed=0, lastInput=-Infinity, nextFrame=0, lastPaint=0;
let answering=false, running=false, failed=false, lastBotPaint=-Infinity;
function install(value, now){
 state=value; changed=now; lastBotPaint=-Infinity; bot.dataset.state=value;
 // Match the visible scene height and baseline to idle, not the source canvas.
 // Square framing keeps the same scale in both narrow and docked bot slots.
 const pose=value==='error'?'idle':value;
 bot.innerHTML=templates[pose];
 const frames={typing:'182 34 434 434',thinking:'18 26 926 926',reading:'177 -24 1086 1086',idle:'0 0 1000 1000'};
 bot.querySelector('svg').setAttribute('viewBox',frames[pose]);
 if(value==='typing'){
 const far=$('#far-arm'),near=$('#near-arm');
 const snap=v=>Math.round(v/4)*4;
function armPath(x,y,length,angle){
 const radians=angle*Math.PI/180, dx=Math.cos(radians),dy=Math.sin(radians);
 const point=(distance,bend=0)=>`${snap(x+dx*distance-dy*bend)} ${snap(y+dy*distance+dx*bend)}`;
 // The elbow, wrist and hand all move together; no isolated wrist flick.
 return `M${x} ${y} L${point(length*.49,4)} L${point(length-12)} L${point(length)} l8 4`;
}
function pose(a,b){
 far.setAttribute('d',armPath(284,239,147,a+5));
 near.setAttribute('d',armPath(284,239,154,b+2));
}

 const poses=[[-18,16],[-13,10],[-3,0],[10,-13],[16,-18],[10,-13],[0,-3],[-13,10]];
 draw=t=>pose(...poses[Math.floor(t/37)%poses.length]);
 }else if(value==='thinking'){
 const bentLeg=$('#bent-leg'),foot=$('#foot');
 const hip={x:625,y:590},toe={x:590,y:867};
function pose(lift){
 // Foot pivots on its grounded toe; upper/lower leg lengths remain constant.
 const angle=(-28-lift*24)*Math.PI/180;
 const ankle={x:toe.x+34*Math.cos(angle),y:toe.y+34*Math.sin(angle)};
 const dx=ankle.x-hip.x,dy=ankle.y-hip.y,d=Math.hypot(dx,dy);
 const length=150, offset=Math.sqrt(Math.max(0,length*length-d*d/4));
 const knee={x:(hip.x+ankle.x)/2+dy/d*offset,y:(hip.y+ankle.y)/2-dx/d*offset};
 bentLeg.setAttribute('d',`M${hip.x} ${hip.y} L${knee.x} ${knee.y} L${ankle.x} ${ankle.y}`);
 // A solid shoe, like the supporting foot. Its toe remains the pivot.
 const shoePoint=(along,height)=>`${toe.x+along*Math.cos(angle)+height*Math.sin(angle)} ${toe.y+along*Math.sin(angle)-height*Math.cos(angle)}`;
 foot.setAttribute('d',`M${shoePoint(0,0)} L${shoePoint(60,0)} L${shoePoint(60,14)} L${shoePoint(38,22)} L${shoePoint(7,14)} Z`);
 return {hip,knee,ankle,toe};
}

 draw=t=>pose((1-Math.cos(t/420*Math.PI*2))/2);
 }else if(value==='reading'){
 function pageShape(progress){
 const theta=progress*Math.PI, dx=200*Math.cos(theta), lift=145*Math.sin(theta);
 const top={x:640+dx,y:450+28*Math.cos(theta)-lift};
 const edge={x:735+dx,y:570+28*Math.cos(theta)-lift};
 const curve=36*Math.sin(theta);
 return {edge,d:`M640 450 Q${640+dx*.55} ${450-lift*.8-curve} ${top.x} ${top.y} L${edge.x} ${edge.y} Q${735+dx*.55} ${570-lift*.8-curve} 735 570Z`};
}
function pose(time){
 // Sweep a page across its fixed spine, release, then return the hand.
 const phase=(time%320)/320, progress=Math.min(phase/.78,1);
 const page=pageShape(progress);
 $('#page').setAttribute('d',page.d);
 $('#page-trail').setAttribute('d',pageShape(Math.max(0,progress-.12)).d);
 let hand=page.edge;
 if(phase>.78){
  const t=(phase-.78)/.22, right=pageShape(0).edge;
  hand={x:hand.x+(right.x-hand.x)*t,y:hand.y+(right.y-hand.y)*t-35*Math.sin(t*Math.PI)};
 }
 $('#turning-arm').setAttribute('d',`M977 365 Q935 ${hand.y-28} ${hand.x+14} ${hand.y-8} L${hand.x} ${hand.y}`);
}


 draw=t=>pose(t/.7);
 }else { draw=t=>$('#seated-body').setAttribute('transform',`rotate(${value==='error'?0:4.5*Math.sin(t/5200*Math.PI*2)} 500 880)`); }
 draw(0);
}
function request(value){wanted=value; wake();}
function visible(){return !document.hidden && document.hasFocus() && window.__prtfmCharacterActive!==false;}
function tick(now){
 nextFrame=0;
 if(!visible())return;
 if(now-lastPaint<33){nextFrame=requestAnimationFrame(tick);return;}lastPaint=now;
 if(wanted!==state && (wanted==='idle'||wanted==='error'||now-changed>=1000))install(wanted,now);
 const typing=now-lastInput<1000 && input.value.length>0;
 // Keep the foot tapping between inputs; only the typing hand rests.
 const pose=[[0,0],[-3,5],[-1,9],[0,4]][Math.floor(now/280)%4];
 const angles=reduced.matches?[0,0]:[typing?pose[0]:0,pose[1]];
 transform(userArm,`rotate(${angles[0]} 355 250)`);
 transform(userFoot,`rotate(${angles[1]} 519 403)`);
 // The slow idle sway moves less than a screen pixel per sample at its displayed size.
 // Keep elapsed-time motion/speed and the existing fast work poses unchanged.
 const botInterval=state==='idle'?1000/15:33;
 if((terminal.open || bot.dataset.outside==='true') && !reduced.matches && state!=='error' && now-lastBotPaint>=botInterval){
  draw(now-changed);lastBotPaint=now;
 }
 if(wanted!==state || !reduced.matches)nextFrame=requestAnimationFrame(tick);
}
function wake(){if(!nextFrame&&visible())nextFrame=requestAnimationFrame(tick);}
input.addEventListener('input',()=>{lastInput=performance.now();wake();});
document.addEventListener('draft-restored',()=>{lastInput=-Infinity;wake();});
terminal.addEventListener('toggle',wake);
document.addEventListener('character-layout',wake);
function suspend(){cancelAnimationFrame(nextFrame);nextFrame=0;}
document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else wake();});
// Windows WebView may remain visibility=visible while its native window is minimized.
window.addEventListener('blur',suspend);
window.addEventListener('focus',wake);
window.addEventListener('character-window-state',()=>{if(visible())wake();else suspend();});
reduced.addEventListener('change',()=>{draw(0);wake();});
document.addEventListener('character-state',({detail:d})=>{
 if(d.type==='start'){running=true;answering=false;failed=false;request('thinking');}
 else if(d.type==='delta'&&running){answering=true;request('typing');}
 else if(d.type==='error'){running=false;failed=true;request('error');}
 else if(d.type==='stop'){running=false;answering=false;request(failed?'error':'idle');}
 else if(d.type==='activity'&&running&&!answering){
 const e=d.event;
 if(d.replaying)return;
 if(d.status==='error'||e?.kind==='error'){failed=true;request('error');}
 else if(['done','cancelled','interrupted'].includes(d.status))request('idle');
 else if((d.events || [e]).some(event=>event?.kind?.startsWith('tool_'))){
 // A fast tool can start AND finish between polls, followed by a model event.
 // Show this newly observed work once; do not replay it on subsequent polls.
 if(state!=='reading')install('reading',performance.now());
 request(e?.kind==='model_request_sent'?'typing':e?.kind?.startsWith('model_')?'thinking':'reading');
 }
 // Responses are validated before delta delivery, so model work must not rely on delta.
 else if(e?.kind==='model_request_sent')request('typing');
 else if(e?.kind?.startsWith('model_'))request('thinking');
 }
});
install('idle',performance.now());wake();
})();
