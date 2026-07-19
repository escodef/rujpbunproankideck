CARD_CSS = """
.card {
    font-family: "Helvetica Neue", Helvetica, "Segoe UI", Arial, "Hiragino Kaku Gothic ProN", Meiryo, sans-serif;
    font-size: 16px;
    text-align: center;
    background-color: #f9fbfd;
    margin: 0;
    padding: 10px;
}
.card-container {
    max-width: 600px;
    margin: 15px auto;
    background: #ffffff;
    border-radius: 12px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.05);
    padding: 25px;
    border: 1px solid #eef2f5;
    color: #2c3e50;
    text-align: center;
}
.nightMode .card-container {
    background: #1e1e24;
    border-color: #2d2d34;
    box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    color: #cbd5e1;
}
.nightMode .card {
    background-color: #121214;
}
.jlpt-tag {
    display: inline-block;
    background: #20b2aa;
    color: white;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 0.8em;
    font-weight: bold;
    margin-bottom: 15px;
}
.grammar-title {
    font-size: 2.2em;
    font-weight: bold;
    color: #2c3e50;
    margin-bottom: 15px;
}
.nightMode .grammar-title {
    color: #f8fafc;
}
.section-title {
    font-size: 0.85em;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #95a5a6;
    margin-top: 20px;
    margin-bottom: 8px;
    font-weight: bold;
    border-bottom: 1px solid #edf2f7;
    padding-bottom: 3px;
}
.nightMode .section-title {
    border-bottom-color: #2d2d34;
}
.meaning-text {
    font-size: 1.25em;
    font-weight: 500;
    color: #27ae60;
}
.box-info {
    font-size: 0.95em;
    text-align: left;
    background: #f8f9fa;
    padding: 12px;
    border-radius: 6px;
    line-height: 1.5;
    border-left: 4px solid #20b2aa;
    color: #34495e;
}
.nightMode .box-info {
    background: #282830;
    color: #cbd5e1;
}
.example-item {
    text-align: left;
    margin-bottom: 12px;
    background: #fdfdfd;
    padding: 10px;
    border-radius: 6px;
    border: 1px solid #f0f0f0;
}
.nightMode .example-item {
    background: #212127;
    border-color: #2d2d34;
}
.ex-jp {
    font-size: 1.2em;
    color: #2980b9;
}
.nightMode .ex-jp {
    color: #38bdf8;
}
.ex-en {
    font-size: 0.9em;
    color: #7f8c8d;
    margin-top: 3px;
}
.cloze-blank {
    color: #e74c3c;
    font-weight: bold;
    border-bottom: 2px dashed #e74c3c;
    padding: 0 4px;
}
.highlight-answer {
    color: #27ae60;
    font-weight: bold;
}
.bunpro-link {
    display: inline-block;
    margin-top: 20px;
    font-size: 0.8em;
    color: #20b2aa;
    text-decoration: none;
    border: 1px solid #20b2aa;
    padding: 5px 16px;
    border-radius: 15px;
    font-weight: 500;
    transition: all 0.15s ease-in-out;
}
.bunpro-link:hover {
    background-color: #20b2aa;
    color: white !important;
}
.nightMode .bunpro-link {
    color: #38bdf8;
    border-color: #38bdf8;
}
.nightMode .bunpro-link:hover {
    background-color: #38bdf8;
    color: #1e1e24 !important;
}
"""

T1_FRONT = """
<div class="card-container">
    <div class="jlpt-tag">{{JLPT}}</div>
    <div class="grammar-title">{{Grammar}}</div>
</div>
"""

T1_BACK = """
<div class="card-container">
    <div class="jlpt-tag">{{JLPT}}</div>
    <div class="grammar-title">{{Grammar}}</div>

    <div class="section-title">Значение</div>
    <div class="meaning-text">{{Meaning}}</div>

    {{#Structure}}
    <div class="section-title">Присоединение</div>
    <div class="box-info">{{Structure}}</div>
    {{/Structure}}

    {{#Nuance}}
    <div class="section-title">Нюансы</div>
    <div class="box-info">{{Nuance}}</div>
    {{/Nuance}}

    {{#ExamplesHTML}}
    <div class="section-title">Примеры</div>
    <div>{{ExamplesHTML}}</div>
    {{/ExamplesHTML}}

    {{#URL}}
    <div style="text-align: center;">
        <a href="{{URL}}" class="bunpro-link">Открыть на Bunpro</a>
    </div>
    {{/URL}}
</div>
"""

T2_FRONT = """
<div class="card-container">
    <div class="jlpt-tag">{{JLPT}}</div>
    <div class="meaning-text" style="font-size: 1.5em; margin: 20px 0;">{{Meaning}}</div>
</div>
"""

T2_BACK = """
<div class="card-container">
    <div class="jlpt-tag">{{JLPT}}</div>
    <div class="grammar-title">{{Grammar}}</div>

    <div class="section-title">Значение</div>
    <div class="meaning-text">{{Meaning}}</div>

    {{#Structure}}
    <div class="section-title">Присоединение</div>
    <div class="box-info">{{Structure}}</div>
    {{/Structure}}

    {{#Nuance}}
    <div class="section-title">Нюансы</div>
    <div class="box-info">{{Nuance}}</div>
    {{/Nuance}}

    {{#ExamplesHTML}}
    <div class="section-title">Примеры</div>
    <div>{{ExamplesHTML}}</div>
    {{/ExamplesHTML}}

    {{#URL}}
    <div style="text-align: center;">
        <a href="{{URL}}" class="bunpro-link">Открыть на Bunpro</a>
    </div>
    {{/URL}}
</div>
"""