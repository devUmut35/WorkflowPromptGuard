# Barındırılan issue botu

[English](issue-bot.md) | **Türkçe**

Barındırılan bot, ziyaretçilerin yapılandırılmış bir issue formundan tek bir herkese açık GitHub
deposunu taramasını sağlar. CLI ile kullanılan aynı deterministik tarayıcının kolaylık katmanıdır.

## İstek akışı

1. Yeni issue sayfasında **Herkese açık depoyu tara / Scan a public repository** seçeneğini açın.
2. Rapor dili olarak **Türkçe** veya **English** seçin.
3. Ayrı bir satıra tam olarak bir `https://github.com/OWNER/REPOSITORY` URL'si girin.
4. Issue'yu gönderin. Form `scan-request` etiketini otomatik ekler.
5. Bot hedefin varsayılan dalının tam commit SHA değerini belirler ve okumaları bu değişmez sürüme
   sabitler.
6. Deterministik rapor ve varsa yapay zekâ açıklaması, seçilen dilde tek bot yorumu olarak
   yayımlanır.

Issue'yu yeniden açmak taramayı tekrar çalıştırır. Mevcut bot yorumu ilk 100 yorum arasındaysa yeni
bir yorum oluşturmak yerine güncellenir.

Her geçerli form isteği için deterministik tarama otomatik çalışır. Herkese açık issue spam'inin
anonim sağlayıcı kotasını tüketmesini önlemek için yapay zekâ açıklaması yalnızca isteği açan
kullanıcının bu WorkflowPromptGuard deposundaki ilişkisi `OWNER`, `MEMBER` veya `COLLABORATOR`
olduğunda otomatik çalışır. Bir depo yöneticisi başka bir istek için `ai-approved` etiketini
ekleyerek yapay zekâyı etkinleştirebilir.

## Kimlik doğrulama ve maliyet

Üçüncü taraf anahtarı veya uzun ömürlü depo secret'ı gerekmez. GitHub, GitHub işlemleri için kısa
ömürlü `GITHUB_TOKEN` kimlik bilgileri oluşturur: tarama job'ı salt okunur GitHub API istekleri
yapar, yorum job'ı ise `issues: write` kullanır. Model isteği `default` seçicisiyle anonim olarak
`https://api.llm7.io/v1/chat/completions` adresine gönderilir. Bu isteğe GitHub tokenı veya
sağlayıcı API anahtarı eklenmez.

LLM7.io şu anda anonim kullanım için saatte 60 istek ve kayan 24 saatlik dönemde toplam 500.000
giriş-çıkış tokenı sınırı belgelemektedir. Anonim kullanım verileri analiz ve model iyileştirme
amacıyla işlenebilir. `default` rotası istekler arasında farklı bir temel model seçebilir; hizmet
seviyesi, kullanılabilirlik veya aynı sonucu yeniden üretme garantisi yoktur. Kota, sağlayıcı,
yanıt doğrulama ya da yönlendirme hatasında bot eksiksiz deterministik raporu kısa bir fallback
notuyla yayımlar. Resmî [LLM7.io hizmet bilgilerini](https://llm7.io/), [anonim
limitleri](https://docs.llm7.io/limits) ve [model seçici
belgesini](https://docs.llm7.io/guides/models) inceleyebilirsiniz.

Bot güvenilen kaynak ağacını doğrudan içe aktarır ve Python 3.13 kullanan, GitHub tarafından
barındırılan bir Linux runner üzerinde yalnızca sürümü ve hash'i sabitlenmiş PyYAML wheel'ini
kurar. Issue ile tetiklenen job sırasında build bağımlılıklarını çözmez.

## Güvenlik kontrolleri

- Hedef URL; sunucu, port, kimlik bilgisi, dal, ref, yol, sorgu veya URL parçası seçemez.
- Yalnızca herkese açık depolar kabul edilir.
- Yalnızca `.github/workflows` altında doğrudan bulunan normal `.yml`, `.yaml` ve `.md` dosyaları
  getirilir.
- Hedefin varsayılan dalı bir kez çözümlenir ve tüm içerik çağrıları tam commit SHA kullanır.
- En fazla 64 iş akışı dosyası, dosya başına 256 KiB ve toplam 2 MiB kabul edilir.
- Hedef depo klonlanmaz; hook, submodule, LFS filtresi, bağımlılık veya hedef kod çalıştırılmaz.
- YAML kaynak boyutu, iç içe geçme, düğüm, alias ve genişletilmiş grafik dolaşımı sınırlıdır.
- Anonim sağlayıcı isteği yalnızca normalize edilmiş `language`, `scanned_files`, `counts` ve
  katalog destekli `rules` agregalarını içerir.
- Depo kimliği, commit SHA, ham issue metni, iş akışı kaynağı, bulgu mesajları, kanıt izleri, dosya
  yolları, GitHub tokenları ve sağlayıcı anahtarları LLM7.io'ya gönderilmez.
- Model hedefi `api.llm7.io/v1/chat/completions`, seçici ise `default` olarak sabittir; yönlendirilen
  temel model değişebilir.
- Tarama, model ve yorum job'ları birbirinden ayrı, en az ayrıcalıklı GitHub izinlerini kullanır.
- Model çıktısı güvenilmeyen veri sayılır; yerelde ayrıştırılıp şemayla doğrulanır, uzunluğu
  sınırlanır, kullanıcı etiketlemeleri etkisizleştirilir ve komut, URL, kimlik ya da API hedefi
  olarak kullanılmaz.

## Operasyonel sınırlar

Issue formu herkese açıktır; bu nedenle GitHub kötüye kullanım kontrolleri ile LLM7.io'nun anonim
sınırları pratik istek hızı sınırlarıdır. Bu sınırlar ve yönlendirilen modeller değişebilir;
LLM7.io anonim rota için hizmet seviyesi garantisi vermez. Yüksek hacimli bir üretim hizmeti harici
kuyruk ve aktör başına hız sınırlayıcı gerektirir. Mevcut bot herkese açık gösterimler ve sınırlı
depo kontrolleri için tasarlanmıştır.

Genel eşzamanlılık grubu aynı anda çalışan işi sınırlar ancak bir hız sınırlayıcı değildir. GitHub
bir grupta yalnızca tek bekleyen çalışma tuttuğu için sürekli issue spam'i geçerli bir bekleyen
taramayı değiştirebilir ve hizmeti geciktirebilir. Yapay zekâ çıkarımı ayrıca güvenilen yazar veya
`ai-approved` kapısıyla korunur; başarısız olması deterministik sonucu değiştirmez.
