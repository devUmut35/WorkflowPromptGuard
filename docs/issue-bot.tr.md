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
ücretsiz model kotasını tüketmesini önlemek için yapay zekâ açıklaması yalnızca isteği açan
kullanıcının bu WorkflowPromptGuard deposundaki ilişkisi `OWNER`, `MEMBER` veya `COLLABORATOR`
olduğunda otomatik çalışır. Bir depo yöneticisi başka bir istek için `ai-approved` etiketini ekleyerek
yapay zekâyı etkinleştirebilir.

## Kimlik doğrulama ve maliyet

Üçüncü taraf anahtarı veya uzun ömürlü depo secret'ı gerekmez. GitHub her job için kısa ömürlü bir
`GITHUB_TOKEN` oluşturur. Tarama job'ı salt okunur GitHub API istekleri yapar; model job'ı
`models: read`, yorum job'ı ise `issues: write` kullanır.

GitHub Models ücretsiz ve hız sınırlı çıkarım içerir. Bu proje ücretli model kullanımını
etkinleştirmez. Ücretsiz kota kullanılamazsa veya GitHub Models isteği reddederse bot, eksiksiz
deterministik raporu kısa bir fallback notuyla yayımlar.

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
- Ham issue metni, iş akışı kaynağı, bulgu mesajı, kanıt izi ve dosya yolu modele gönderilmez.
- Dil seçimi kapalı `tr` veya `en` kümesine indirgenir; ham form metni modele gönderilmez.
- Model yalnızca katalog destekli kural kimliklerini, önem derecelerini, sayımları ve çözüm
  metinlerini alır.
- Tarama, model ve yorum job'ları birbirinden ayrı, en az ayrıcalıklı token'lar kullanır.
- Model çıktısı şema ile doğrulanır, uzunluğu sınırlanır, mention'lar etkisizleştirilir ve komut,
  URL, kimlik ya da API hedefi olarak kullanılmaz.

## Operasyonel sınırlar

Issue formu herkese açıktır; bu nedenle GitHub kötüye kullanım kontrolleri ile GitHub Models
ücretsiz kotası pratik istek hızı sınırlarıdır. Yüksek hacimli bir üretim hizmeti harici kuyruk ve
aktör başına rate limiter gerektirir. Mevcut bot herkese açık gösterimler ve sınırlı depo
kontrolleri için tasarlanmıştır.

Global concurrency grubu aynı anda çalışan işi sınırlar ancak bir rate limiter değildir. GitHub
bir grupta yalnızca tek bekleyen çalışma tuttuğu için sürekli issue spam'i geçerli bir bekleyen
taramayı değiştirebilir ve hizmeti geciktirebilir. Yapay zekâ çıkarımı ayrıca güvenilen yazar veya
`ai-approved` kapısıyla korunur.
