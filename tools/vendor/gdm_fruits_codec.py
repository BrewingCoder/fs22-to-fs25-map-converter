"""Pure-Python multi-range/type-index GDM codec (densityMap_fruits). Round-trip self-test:
decode real fruits.gdm -> re-encode -> the two must produce the SAME grleConverter PNG."""
import struct, os, sys
import numpy as np

def _dec_record(d,pos):
    npl=d[pos]; pc=d[pos+1]; p=pos+2
    pal=[struct.unpack_from("<H",d,p+2*i)[0] for i in range(pc)]; p+=2*pc
    if npl==0:
        return np.full(1024,pal[0] if pc>=1 else 0,np.uint16),p
    planes=np.frombuffer(d[p:p+npl*128],np.uint8); p+=npl*128
    bits=np.unpackbits(planes,bitorder="little")[:npl*1024].reshape(1024,npl)  # INTERLEAVED per pixel
    idx=(bits.astype(np.uint16)*(1<<np.arange(npl,dtype=np.uint16))).sum(1)
    if pc==0: return idx,p                        # raw mode: value = index
    pa=np.array(pal,np.uint16); return pa[np.clip(idx,0,pc-1)],p

def _enc_record(vals, width):
    flat=np.ascontiguousarray(vals.reshape(-1))
    _,first=np.unique(flat,return_index=True)
    pal=flat[np.sort(first)]; pc=len(pal)
    if pc==1: return bytes((0,1))+struct.pack("<H",int(pal[0]))
    if pc<=4:                                     # PALETTE mode (nplanes 1-2, value=palette[idx])
        npl=max(1,(pc-1).bit_length())
        lut={int(v):i for i,v in enumerate(pal)}
        idx=np.array([lut[int(v)] for v in flat],np.uint16)
        pal_bytes=b"".join(struct.pack("<H",int(v)) for v in pal)
        head=bytes((npl,pc))+pal_bytes
    else:                                          # RAW mode (nplanes=width, pc=0, value=index)
        npl=width; idx=flat.astype(np.uint16); head=bytes((npl,0))
    bits=((idx[:,None]>>np.arange(npl,dtype=np.uint16))&1).reshape(-1).astype(np.uint8)  # INTERLEAVED
    return head+np.packbits(bits,bitorder="little").tobytes()

def roundtrip(src, dst):
    d=open(src,"rb").read()
    msl,csl,maxbpp,nch,nranges,ntic,b14,b15=d[8:16]
    hdr_end=16+(nranges-1)+b14*3
    splits=[0]+list(d[16:16+nranges-1])+[nch]     # channel boundaries per compression range
    widths=[splits[r+1]-splits[r] for r in range(nranges)]   # e.g. [6,5]
    out=bytearray(d[:hdr_end])                    # header + range-split + mappings unchanged
    ncells=((1<<(msl+5))//(1<<csl))**2
    pos=hdr_end
    for ci in range(ncells):
        for r in range(nranges):
            vals,pos=_dec_record(d,pos)
            out+=_enc_record(vals,widths[r])
    open(dst,"wb").write(out)
    return len(d),len(out)

def read_header(src):
    d=open(src,"rb").read()
    msl,csl,maxbpp,nch,nranges,ntic,b14,b15=d[8:16]
    splits=[0]+list(d[16:16+nranges-1])+[nch]
    return dict(data=d,mapsize=1<<(msl+5),chunk=1<<csl,nranges=nranges,nch=nch,ntic=ntic,
                b14=b14,b15=b15,splits=splits,widths=[splits[r+1]-splits[r] for r in range(nranges)],
                hdr_end=16+(nranges-1)+b14*3)

def decode_full(src):
    """-> (values uint16 [mapsize,mapsize]) where value = sum(range_val << range_first_channel)."""
    h=read_header(src); d=h["data"]; N=h["mapsize"]; C=h["chunk"]; nch=N//C
    vals=np.zeros((N,N),np.uint16); pos=h["hdr_end"]
    for cr in range(nch):
        for cc in range(nch):
            cell=np.zeros(1024,np.uint32)
            for r in range(h["nranges"]):
                rv,pos=_dec_record(d,pos)
                cell |= (rv.astype(np.uint32) << h["splits"][r])
            vals[cr*C:(cr+1)*C, cc*C:(cc+1)*C]=cell.reshape(C,C)
    return vals

def encode_full(vals, template_src, dst):
    """Encode a full mapsize value array back to a .gdm using template's header/ranges. Inverse of decode_full."""
    h=read_header(template_src); N=h["mapsize"]; C=h["chunk"]; nch=N//C
    out=bytearray(h["data"][:h["hdr_end"]])
    splits=h["splits"]; widths=h["widths"]
    masks=[(1<<widths[r])-1 for r in range(h["nranges"])]
    for cr in range(nch):
        for cc in range(nch):
            block=vals[cr*C:(cr+1)*C, cc*C:(cc+1)*C].reshape(-1).astype(np.uint32)
            for r in range(h["nranges"]):
                rv=((block >> splits[r]) & masks[r]).astype(np.uint16)
                out+=_enc_record(rv.reshape(C,C), widths[r])
    open(dst,"wb").write(bytes(out))
    return len(out)

if __name__=="__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python gdm_fruits_codec.py <densityMap_fruits.gdm> [reencoded_out.gdm]")
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src + ".reenc"
    a, b = roundtrip(src, dst); print(f"src {a} bytes -> reenc {b} bytes ({100*b/a:.1f}%)  wrote {dst}")
