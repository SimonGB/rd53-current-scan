"""Read-only extraction of board figure candidates from a local thesis PDF."""

def candidates(path):
    import fitz
    result=[];seen=set()
    with fitz.open(path) as doc:
        for i,page in enumerate(doc):
            text=page.get_text().lower()
            relevant=any(w in text for w in ('testplatine','testboard','test board','prüfplatine','leiterplatte'))
            for entry in page.get_images(full=True):
                xref=entry[0]
                if xref in seen:continue
                seen.add(xref);image=doc.extract_image(xref)
                if image['width']<180 or image['height']<100:continue
                result.append(dict(page=i+1,width=image['width'],height=image['height'],
                    data=image['image'],extension=image['ext'],relevant=relevant))
    result.sort(key=lambda x:(not x['relevant'],x['page']))
    return result
