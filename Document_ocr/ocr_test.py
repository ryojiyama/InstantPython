import os, json, io
from dotenv import load_dotenv
from google.cloud import vision
from pdf2image import convert_from_path

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")

def ocr_pdf(pdf_path):
    images = convert_from_path(pdf_path, dpi=300)
    client = vision.ImageAnnotatorClient(
        client_options={"api_key": API_KEY}
    )

    results = []
    for i, image in enumerate(images):
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        content = buf.getvalue()

        vision_image = vision.Image(content=content)
        response = client.document_text_detection(image=vision_image)

        page_data = {
            "page": i + 1,
            "full_text": response.full_text_annotation.text,
            "blocks": []
        }

        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                block_text = ""
                for para in block.paragraphs:
                    for word in para.words:
                        block_text += "".join([s.text for s in word.symbols])
                vertices = block.bounding_box.vertices
                page_data["blocks"].append({
                    "text": block_text,
                    "confidence": round(block.confidence, 3),
                    "x": vertices[0].x,
                    "y": vertices[0].y
                })

        results.append(page_data)
        print(f"ページ {i+1} 処理完了")

    return results

pdf_path = "./docs/処置票テスト.pdf"
results = ocr_pdf(pdf_path)

os.makedirs("results", exist_ok=True)
with open("./results/vision_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print("\n=== 抽出テキスト ===")
for page in results:
    print(page["full_text"])

print("\nJSON保存先: ./results/vision_result.json")
