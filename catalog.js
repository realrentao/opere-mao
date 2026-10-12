// catalog.js — 目录页逻辑
(function () {
  'use strict';
  var DATA = window.__GRIMM_INDEX__;
  var grid = document.getElementById('grid');
  var search = document.getElementById('search');
  var stat = document.getElementById('stat');

  function esc(s) {
    return (s || '').replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function render(filter) {
    filter = (filter || '').trim().toLowerCase();
    var list = DATA.stories.filter(function (s) {
      if (!filter) return true;
      return (
        (s.title_it || '').toLowerCase().indexOf(filter) >= 0 ||
        (s.title_zh || '').toLowerCase().indexOf(filter) >= 0 ||
        s.id.indexOf(filter) >= 0
      );
    });
    grid.innerHTML = list
      .map(function (s) {
        return (
          '<a class="card" href="lettura.html?id=' + encodeURIComponent(s.id) + '">' +
          '<span class="card-no">' + s.id + '</span>' +
          '<span class="card-it"><span class="card-dot"></span>' + esc(s.title_it) + '</span>' +
          '<span class="card-zh">' + esc(s.title_zh) + '</span>' +
          '</a>'
        );
      })
      .join('');
    stat.textContent =
      filter
        ? '找到 ' + list.length + ' / ' + DATA.stories.length + ' 篇'
        : '共 ' + DATA.stories.length + ' 篇著作 · 点击阅读';
  }

  search.addEventListener('input', function () {
    render(search.value);
  });
  render('');
})();
