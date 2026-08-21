import base64
import io
import zipfile


def test_markdown_import_is_normalized_segmented_and_inspectable(client):
    text = "# 火星来信\n\n" + "维修工程师在火星沙暴中寻找失联机器人，并追踪反复出现的求救信号。\n\n" * 30
    response = client.post("/api/imports/inspect", json={
        "filename": "story.md",
        "content_text": text,
        "max_segment_chars": 500,
        "overlap_chars": 50,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["format"] == "md"
    assert result["character_count"] > 500
    assert len(result["segments"]) > 1
    assert all(len(item["checksum_sha256"]) == 64 for item in result["segments"])


def test_docx_can_create_project_without_external_parser(client):
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>深海潜艇的声呐员听见失踪潜航员从沉船里发出的回声。</w:t></w:r></w:p>
        <w:p><w:r><w:t>她决定潜入海沟，在水压摧毁舱体前寻找真相。</w:t></w:r></w:p>
      </w:body>
    </w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    response = client.post("/api/imports/projects", json={
        "name": "深蓝回声",
        "filename": "screenplay.docx",
        "content_base64": base64.b64encode(buffer.getvalue()).decode(),
        "target_duration_sec": 50,
    })
    assert response.status_code == 201, response.text
    project = response.json()
    assert "深海潜艇" in project["source_text"]
    assert project["agent_trace"][0]["node"] == "document_ingestion"
    assert project["agent_trace"][0]["format"] == "docx"


def test_structured_screenplay_json_and_invalid_documents(client):
    screenplay = '{"scenes":[{"heading":"火星基地 内景","action":"维修工程师收到失联机器人的求救信号。"}]}'
    parsed = client.post("/api/imports/inspect", json={
        "filename": "script.json", "content_text": screenplay,
    })
    assert parsed.status_code == 200
    assert "heading: 火星基地" in parsed.json()["preview"]
    assert client.post("/api/imports/inspect", json={
        "filename": "payload.exe", "content_text": "a" * 30,
    }).status_code == 422
    assert client.post("/api/imports/inspect", json={
        "filename": "story.txt", "content_text": "a" * 30, "content_base64": "YWJj",
    }).status_code == 422

