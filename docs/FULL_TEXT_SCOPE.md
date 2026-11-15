# Tam makale okuma: kapsam kararı

7 Eylül 2026 — pm-busra `decide` / `spec` yaklaşımıyla, @aytug'un talebi ve yerel kod incelemesi.

Karar (kullanıcı düzeltmesiyle): Paper Pilot'un görevi doğru makaleyi bulmak, PDF'yi indirmek,
metin katmanının tamamını ve AI ihtiyaç duyduğunda özgün PDF dosyasını Codex/Claude'a ulaştırmak.
Metin, PDF veya belirli sayfa görüntüleri arasındaki seçimi göreve göre AI yapar.
Tam makale PDF'si mevcutsa doğrudan kullanılır. Unpaywall; PDF bağlantısı bulunamadığında,
indirme başarısız olduğunda veya AI eldeki dosyanın yalnız kapak/abstract olduğunu gördüğünde
DOI üzerinden devreye girer. DOI'siz PDF'ler için uygulanamaz olduğu açıkça belirtilir.
Sentezi zaten istemcideki model yapıyor;
sunucuya ikinci bir model veya yeni araştırma platformu eklemeye gerek yok.

Sorun: tam metin diskte >> modele yalnız seçili kısa parçalar gidiyor >> yöntem, sonuç ve
ekler atlanabiliyor >> kullanıcı makalenin tamamının okunduğunu sanıyor.

Kanıt: başlangıç kodunda `DeepReadArtifact.to_dict()` beş parçayı 1.200'er karakterle sınırlandırıyordu;
`extract_document()` tam metni yerel dosyaya yazıyordu. Mevcut `get_pdf_page_text` tekil sayfaları
veriyordu, fakat uzun bir sayfayı güvenli boyutta sürdürmek için devam noktası yoktu.
Kaynaklar: [models.py](../src/paper_pilot/models.py), [deep_read.py](../src/paper_pilot/services/deep_read.py),
[server.py](../src/paper_pilot/server.py). Pazar veya kullanım oranı verisi yok; sayı uydurulmadı.

| Alternatif | Karar ve gerekçe |
|---|---|
| YÖNTEM 1: AI istediğinde özgün PDF'yi ulaştırmak | KAPSAMDA. `read_pdf_document`: yerel dosya, MCP kaynağından PDF baytları veya destekleyen istemcide gömülü PDF. Yalnız yol döndürmek dosyanın okunmuş olduğu anlamına gelmez. |
| YÖNTEM 2: mevcut PDF ayrıştırıcısıyla sıralı metin ve devam noktası | Seçildi. Yeni bağımlılık yok; standart MCP metin yanıtıyla çalışıyor. |
| YÖNTEM 3: her arama yanıtına koşulsuz PDF gömmek | (İPTAL) AI'ın ihtiyacına göre PDF istemesi yeterli; gömme mevcut boyut/sayfa sınırlarına ve istemci desteğine bağlı. |
| YÖNTEM 4: OCR / taranmış belge işleme | KAPSAM DIŞI. Kullanıcının açık kararı; kodda, MCP parametrelerinde veya kurulumda bulunmayacak. |

| Gerekli davranış | Bu değişiklik |
|---|---|
| Doğru kaydı seçmek | Başlıkla birlikte yazar, yıl ve DOI/arXiv kimliği kontrol edilir. arXiv sorgusunda başlık ifadesi ve yıl aralığı korunur. |
| İndirmek | Mevcut OA indirme servisi kullanılır. İndirilemeyen dosya okunmuş sayılmaz. |
| Unpaywall kullanmak | Mevcut PDF yolu çalışıyorsa ek sorgu yok. PDF eksik/hatalıysa veya AI içeriği yetersiz bulursa DOI ile `best_oa_location` ve diğer `oa_locations` denenir; lisans, sürüm ve kaynak korunur. |
| Tam metni ulaştırmak | `read_pdf_text`, sayfa ve karakter konumuyla devam eder. `deep_read_topic` ilk metin grubunu doğrudan verir. |
| İhtiyaç halinde PDF'yi ulaştırmak | AI `read_pdf_document` çağırır. Dosya erişimi varsa PDF'yi açar; yoksa kaynak bağlantısından baytları alır veya istemci destekliyorsa `embed_base64=true` kullanır. Metin okumayı bitirmesi önkoşul değildir. |
| Eksikleri saklamamak | Metinsiz sayfalar ve bozuk dosyalar bildirilir. Tek dosya hatası diğer belgeleri düşürmez. |
| Kanıtı göstermek | Kaynak PDF ve sayfa numarası. Şekil/tablo gerektiğinde mevcut sayfa görüntüleme aracı. |

Bu işin dışında: OCR, vektör veritabanı, embedding/RAG katmanı, sunucu içinde LLM,
yeni arama sağlayıcısı, hesap sistemi, yeni arayüz, zamanlayıcı, otomatik yayın ve zorunlu Zotero.
Mevcut Zotero ve grafik özellikleri isteğe bağlı kalır; tam metin okumaya önkoşul olmaz.

Metin üzerinden okuma sözleşmesi: ilk sayfa/karakter 0'dan başlanır, her `next_cursor` izlenir, null olduğunda
erişilebilir metnin sonuna ulaşılmış olur. Bu sınır, modelin önceki yanıtları okuduğunun kanıtı değildir.
Metin katmanı olmayan sayfa varsa kullanıcıya eksik kapsam bildirilir; abstract ile boşluk doldurulmaz.
Rapor ve `top_chunks` bir okuma paketi olarak etiketlenir; tamamlanmış sentez gibi sunulmaz.
Doğrudan PDF seçilirse model dosyayı inceleyerek kapsamını belirtir. Kaynak bağlantısı veya
PDF baytları almak tek başına okuma kanıtı değildir. İstemci PDF'yi modele gösteremiyorsa bu
sınır açıkça belirtilir; mevcut metin veya sayfa görüntüsü araçları kullanılır.

ITR-1 bu akışın kodu, dar kapsamlı regresyon kontrolleri ve gerçek PDF ile MCP doğrulamasıdır.
ITR-2 tanımlanmadı; şimdiden başka özellik planlamaya gerek yok.

7 Eylül 2026 ek talep: Kullanıcının ucuz toprak pH devresi örneğiyle istediği disiplinler
arası araştırma ve karar yönlendirmesi, [Araştırmadan uygulama kararına](RESEARCH_DECISIONS.md)
belgesinde ayrı kapsam olarak değerlendirildi. Buradaki PDF/Unpaywall sınırları korunuyor.

Unpaywall kararı: Kullanıcının paylaştığı [roadoi rehberi](https://cran.r-project.org/web/packages/roadoi/vignettes/intro.html)
(26 Eylül 2024) DOI üzerinden OA kopyalarını, tüm erişim konumlarını ve lisans/kaynak bilgisini açıklıyor.
Mevcut Python istemcisi aynı Unpaywall v2 API'sini kullanır; R veya roadoi çalışma zamanı eklenmez.
Unpaywall bir DOI erişim çözümleyicisidir; mevcut tam PDF'ye erişimin önkoşulu değildir.

7 Eylül 2026 düzeltmesi: her DOI'yi koşulsuz kontrol etmek (İPTAL) — çalışan tam PDF varken
gereksiz çağrı ve yapılandırma zorunluluğu yaratıyordu. Güncel kural: mevcut PDF'yi önce dene;
erişim sorunu varsa Unpaywall'a başvur. PDF'siz kayıtların ilk 20 DOI ile kesilmesi kaldırılmıştır.
Yalnız en iyi bağlantı → kullanılabilir diğer OA PDF konumlarıyla devam.
Sessiz OpenAlex dönüşü → Unpaywall hatası görünür, OpenAlex'in alternatif kaynak olduğu açık.
E-posta ayarı isteğe bağlıdır. Unpaywall sırasıyla `UNPAYWALL_EMAIL`, `OPENALEX_EMAIL`,
ikisi de boşsa kullanıcının istediği `nomail@mail.com` adresini kullanır.
API hatası olursa hata görünür kalır ve OpenAlex alternatifi denenir.
PDF dosyasının açılması, tam makale olduğuna karar vermek için yeterli değildir; bunu AI içerikten
değerlendirir. Kapak/abstract çıktıysa `download_paper(doi="...")` ile
Unpaywall yolu istenir. Sunucu sayfa sayısından tam makale sertifikası üretmez.
Geçerli API önbelleği kullanılabilir; süresi dolan Unpaywall kaydı bağlantı hatasını gizlemek için kullanılmaz.
`url_for_landing_page` PDF sayılmaz; indirmede `url_for_pdf` kullanılır. Gelecekte açılacak
`oa_locations_embargoed` bugünkü indirme adayı değildir. İndirilen kopyanın URL ve konum bilgisi kaydedilir.

Açık soru: @aytug — Claude Desktop ve Codex'in belirli sürümlerindeki kullanıcı oturumu davranışı
bu çalışma sırasında doğrulanmış sayılmayacak. Önce standart MCP üzerinden metnin tamamının taşındığı
doğrulanır; istemciye özgü yeni iş ancak somut bir engel görülürse açılır.

## [İYİLEŞTİRME] Doğrulama kanıtı

7 Eylül 2026, gerçek stdio MCP bağlantısı: `healthcheck` → `deep_read_topic` → `read_pdf_text`
devam çağrıları. [Attention Is All You Need](https://arxiv.org/abs/1706.03762), Vaswani vd., 2017,
arXiv `1706.03762v7` bulundu ve indirildi. 15 sayfadaki 39.498 karakter dört yanıtta taşındı;
birleştirilen çıktı PDF'nin tüm sayfalarının metin çıkarımıyla birebir eşleşti. Son sayfa 781 karakterdi;
sonuç ve kaynakça bölümleri de iletildi. Mevcut sayfa görüntüleme aracı ayrıca MCP görüntü bloğu döndürdü.

Yerel kanıt: `data/full-text-mcp-verification.json`; PDF SHA-256:
`bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`.
Bu, metnin taşındığının kanıtıdır; modelin anladığının veya bütün şekilleri incelediğinin kanıtı değildir.
Semantic Scholar 429 döndürdü; diğer kaynaklarla akış tamamlandı. Codex/Claude arayüz oturumları test edilmedi.

90 test geçti; lint, paket oluşturma ve dağıtım dosyası kontrolleri geçti. Yeni çalışma zamanı bağımlılığı yok.
Arama düzeltmesinin API dayanağı: [arXiv sorgu kuralları](https://info.arxiv.org/help/api/user-manual.html#query_details).

PDF kapsamı düzeltmesinden sonra aynı 2.215.244 baytlık PDF, gerçek stdio MCP üzerinden hem
`read_pdf_document` kaynak bağlantısı okunarak hem de `embed_base64=true` ile alındı.
İki aktarım da özgün dosyayla bayt düzeyinde aynıydı. Kanıt: `data/pdf-delivery-verification.json`.
Mevcut dört PDF erişim testi ve lint geçti. Bu kontrol istemci arayüzünün PDF'yi modele
gösterdiğini doğrulamıyor; PDF'nin MCP üzerinden eksiksiz taşındığını doğruluyor.

Unpaywall kapsamı için rehberdeki `10.1186/s12864-016-2566-9` DOI'siyle gerçek servis kontrolü
yapıldı: Unpaywall üç OA konumu döndürdü; PDF onun yayıncı bağlantısından indirildi.
İndirilen kopyanın `cc-by` lisansı ve `publishedVersion` sürümü kaydedildi; 13 sayfanın
63.079 karakteri sıralı okuma aracıyla alındı. Kanıt: `data/unpaywall-verification.json`.
PDF SHA-256: `c2548b2d2b1c159c8ae2930444167c526ed6b2f13cbfe4f473461d733d6e9bc8`.
Önceki iterasyonda 95 test ve lint geçmişti; e-posta eksikliği, 20 üzeri DOI, mevcut PDF,
alternatif PDF konumu, landing-page ayrımı ve süresi geçmiş önbellekte servis hatası kontrol edildi.

Koşullu Unpaywall düzeltmesinden sonra 99 test ve lint geçti. Çalışan PDF için Unpaywall'a
çağrı yapılmadığı, bağlantı başarısızsa veya yalnız DOI verilirse çözümleyicinin kullanıldığı
kontrol edildi. PDF'si hazır kayıtlar e-posta yapılandırması olmadan da kullanılabiliyor.
