#define _GNU_SOURCE

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <va/va.h>
#include <va/va_drmcommon.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vaapi.h>
#include <libavutil/mem.h>
#include <libavutil/pixfmt.h>
#include <libavutil/sha.h>

static const char expected_input_sha256[] =
   "d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2";
static const char expected_driver_sha256[] =
   "64c5508bb7167947f932c950d5e2d2c314f631fc3919de82f757c3769758b79f";

static int
sha256_file(const char *path, char hex[65])
{
   FILE *file = fopen(path, "rb");
   struct AVSHA *sha = NULL;
   uint8_t buffer[1024 * 1024];
   uint8_t digest[32];
   size_t count;

   if (!file)
      return 1;
   sha = av_sha_alloc();
   if (!sha || av_sha_init(sha, 256) < 0) {
      fclose(file);
      av_free(sha);
      return 1;
   }
   while ((count = fread(buffer, 1, sizeof(buffer), file)) > 0)
      av_sha_update(sha, buffer, count);
   if (ferror(file)) {
      fclose(file);
      av_free(sha);
      return 1;
   }
   fclose(file);
   av_sha_final(sha, digest);
   av_free(sha);
   for (size_t i = 0; i < sizeof(digest); i++)
      snprintf(hex + i * 2, 3, "%02x", digest[i]);
   hex[64] = '\0';
   return 0;
}

static int
verify_input_sha256(const char *path)
{
   char hex[65];
   if (sha256_file(path, hex)) {
      fprintf(stderr, "cannot open pinned input %s: %s\n", path, strerror(errno));
      return 1;
   }
   printf("INPUT path=%s sha256=%s\n", path, hex);
   if (strcmp(hex, expected_input_sha256)) {
      fprintf(stderr, "pinned input SHA-256 mismatch\n");
      return 1;
   }
   return 0;
}

static int
prepare_va_driver(const char *driver_dir, const char *expected_driver_path,
                  char resolved_driver[PATH_MAX])
{
   char link_path[PATH_MAX];
   char *resolved_link;
   char *resolved_expected;
   char actual_sha256[65] = {0};
   if (!driver_dir || !driver_dir[0]) {
      fprintf(stderr, "VA driver search path is empty\n");
      return 1;
   }
   int length = snprintf(link_path, sizeof(link_path),
                         "%s%snouveau_drv_video.so", driver_dir,
                         driver_dir[strlen(driver_dir) - 1] == '/' ? "" : "/");
   if (length < 0 || (size_t)length >= sizeof(link_path)) {
      fprintf(stderr, "VA driver search path is too long\n");
      return 1;
   }

   resolved_link = realpath(link_path, NULL);
   resolved_expected = realpath(expected_driver_path, NULL);
   if (!resolved_link || !resolved_expected ||
       strcmp(resolved_link, resolved_expected)) {
      fprintf(stderr,
              "VA driver alias does not resolve to the pinned DSO: "
              "alias=%s expected=%s\n",
              resolved_link ? resolved_link : "<unresolved>",
              resolved_expected ? resolved_expected : "<unresolved>");
      free(resolved_link);
      free(resolved_expected);
      return 1;
   }
   if (sha256_file(resolved_expected, actual_sha256) ||
       strcmp(actual_sha256, expected_driver_sha256)) {
      fprintf(stderr,
              "VA driver DSO SHA-256 mismatch: path=%s actual=%s expected=%s\n",
              resolved_expected,
              actual_sha256,
              expected_driver_sha256);
      free(resolved_link);
      free(resolved_expected);
      return 1;
   }
   if (strlen(resolved_expected) >= PATH_MAX) {
      fprintf(stderr, "resolved VA driver path is too long\n");
      free(resolved_link);
      free(resolved_expected);
      return 1;
   }
   strcpy(resolved_driver, resolved_expected);
   printf("VA_DRIVER_EXPECTED name=nouveau path=%s sha256=%s\n",
          resolved_driver, actual_sha256);
   free(resolved_link);
   free(resolved_expected);

   if (setenv("LIBVA_DRIVER_NAME", "nouveau", 1) ||
       setenv("LIBVA_DRIVERS_PATH", driver_dir, 1)) {
      fprintf(stderr, "cannot set pinned libva driver selection: %s\n",
              strerror(errno));
      return 1;
   }
   return 0;
}

static int
verify_va_driver_mapped(const char *expected_driver_path)
{
   FILE *maps = fopen("/proc/self/maps", "r");
   char *expected = realpath(expected_driver_path, NULL);
   char *line = NULL;
   size_t capacity = 0;
   int found = 0;

   if (!maps || !expected) {
      fprintf(stderr, "cannot inspect mapped VA driver DSO\n");
      if (maps)
         fclose(maps);
      free(expected);
      return 1;
   }

   while (getline(&line, &capacity, maps) >= 0) {
      char *path = strchr(line, '/');
      if (!path)
         continue;
      path[strcspn(path, "\n")] = '\0';
      size_t path_length = strlen(path);
      static const char deleted_suffix[] = " (deleted)";
      if (path_length >= sizeof(deleted_suffix) - 1 &&
          !strcmp(path + path_length - (sizeof(deleted_suffix) - 1),
                  deleted_suffix))
         continue;
      char *resolved = realpath(path, NULL);
      if (resolved && !strcmp(resolved, expected))
         found = 1;
      free(resolved);
      if (found)
         break;
   }
   free(line);
   fclose(maps);
   if (!found) {
      fprintf(stderr, "pinned Nouveau VA driver DSO is not mapped\n");
      free(expected);
      return 1;
   }

   char actual_sha256[65] = {0};
   if (sha256_file(expected, actual_sha256) ||
       strcmp(actual_sha256, expected_driver_sha256)) {
      fprintf(stderr, "mapped VA driver DSO changed after load\n");
      free(expected);
      return 1;
   }
   printf("VA_DRIVER_MAPPED path=%s sha256=%s\n", expected, actual_sha256);
   free(expected);
   return 0;
}

static enum AVPixelFormat hw_pixel_format = AV_PIX_FMT_NONE;

static enum AVPixelFormat
select_vaapi_format(AVCodecContext *context, const enum AVPixelFormat *formats)
{
   (void)context;
   for (const enum AVPixelFormat *format = formats;
        *format != AV_PIX_FMT_NONE; format++) {
      if (*format == hw_pixel_format)
         return *format;
   }
   return AV_PIX_FMT_NONE;
}

static int
find_vaapi_hw_format(const AVCodec *codec)
{
   for (int index = 0;; index++) {
      const AVCodecHWConfig *config = avcodec_get_hw_config(codec, index);
      if (!config)
         return AVERROR(ENOSYS);
      if (config->device_type == AV_HWDEVICE_TYPE_VAAPI &&
          (config->methods & AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX)) {
         hw_pixel_format = config->pix_fmt;
         return 0;
      }
   }
}

static int
query_surface_contract(VADisplay display, VAConfigID *config_out)
{
   int max_profiles = vaMaxNumProfiles(display);
   if (max_profiles <= 0) {
      fprintf(stderr, "vaMaxNumProfiles returned %d\n", max_profiles);
      return 1;
   }
   VAProfile *profiles = calloc((size_t)max_profiles, sizeof(*profiles));
   if (!profiles) {
      fprintf(stderr, "profile-list allocation failed\n");
      return 1;
   }
   int profile_count = 0;
   VAStatus status = vaQueryConfigProfiles(display, profiles, &profile_count);
   if (status != VA_STATUS_SUCCESS) {
      fprintf(stderr, "vaQueryConfigProfiles failed: %d (%s)\n",
              status, vaErrorStr(status));
      free(profiles);
      return 1;
   }

   int found_profile = 0;
   for (int i = 0; i < profile_count; i++)
      found_profile |= profiles[i] == VAProfileH264High;
   free(profiles);
   if (!found_profile) {
      fprintf(stderr, "VAProfileH264High is not advertised\n");
      return 1;
   }

   int max_entrypoints = vaMaxNumEntrypoints(display);
   if (max_entrypoints <= 0) {
      fprintf(stderr, "vaMaxNumEntrypoints returned %d\n", max_entrypoints);
      return 1;
   }
   VAEntrypoint *entrypoints = calloc((size_t)max_entrypoints,
                                      sizeof(*entrypoints));
   if (!entrypoints) {
      fprintf(stderr, "entrypoint-list allocation failed\n");
      return 1;
   }
   int entrypoint_count = 0;
   status = vaQueryConfigEntrypoints(display, VAProfileH264High,
                                     entrypoints, &entrypoint_count);
   if (status != VA_STATUS_SUCCESS) {
      fprintf(stderr, "vaQueryConfigEntrypoints failed: %d (%s)\n",
              status, vaErrorStr(status));
      free(entrypoints);
      return 1;
   }

   int found_vld = 0;
   for (int i = 0; i < entrypoint_count; i++)
      found_vld |= entrypoints[i] == VAEntrypointVLD;
   free(entrypoints);
   if (!found_vld) {
      fprintf(stderr, "VAEntrypointVLD is not advertised for H264 High\n");
      return 1;
   }

   /* FFmpeg n8.0.1 vaapi_decode.c passes NULL, 0 for this config. */
   printf("CONFIG profile=H264High entrypoint=VLD attrib_count=0\n");
   status = vaCreateConfig(display, VAProfileH264High, VAEntrypointVLD,
                           NULL, 0, config_out);
   if (status != VA_STATUS_SUCCESS) {
      fprintf(stderr, "vaCreateConfig failed: %d (%s)\n",
              status, vaErrorStr(status));
      return 1;
   }

   unsigned int count = 0;
   status = vaQuerySurfaceAttributes(display, *config_out, NULL, &count);
   if (status != VA_STATUS_SUCCESS || count == 0) {
      fprintf(stderr, "vaQuerySurfaceAttributes(count) failed: %d (%s)\n",
              status, vaErrorStr(status));
      return 1;
   }

   VASurfaceAttrib *attributes = calloc(count, sizeof(*attributes));
   if (!attributes) {
      fprintf(stderr, "surface attribute allocation failed\n");
      return 1;
   }
   unsigned int returned = count;
   status = vaQuerySurfaceAttributes(display, *config_out,
                                     attributes, &returned);
   if (status != VA_STATUS_SUCCESS) {
      fprintf(stderr, "vaQuerySurfaceAttributes(values) failed: %d (%s)\n",
              status, vaErrorStr(status));
      free(attributes);
      return 1;
   }

   int prime2_advertised = 0;
   for (unsigned int i = 0; i < returned; i++) {
      const VASurfaceAttrib *attribute = &attributes[i];
      if (attribute->value.type != VAGenericValueTypeInteger)
         continue;
      if (attribute->type == VASurfaceAttribMemoryType) {
         uint32_t mask = (uint32_t)attribute->value.value.i;
         prime2_advertised = (mask & VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2) != 0;
         printf("SURFACE_ATTR type=MemoryType flags=%#x value=%#x\n",
                attribute->flags, mask);
      } else if (attribute->type == VASurfaceAttribPixelFormat) {
         printf("SURFACE_ATTR type=PixelFormat flags=%#x value=%#x\n",
                attribute->flags, (uint32_t)attribute->value.value.i);
      } else if (attribute->type == VASurfaceAttribUsageHint) {
         printf("SURFACE_ATTR type=UsageHint flags=%#x value=%#x\n",
                attribute->flags, (uint32_t)attribute->value.value.i);
      }
   }
   free(attributes);
   printf("CONTRACT drm_prime_2_advertised=%s\n",
          prime2_advertised ? "true" : "false");
   return 0;
}

static int
try_export_surface(VADisplay display, VASurfaceID surface, const AVFrame *frame)
{
   VAStatus status = vaSyncSurface(display, surface);
   if (status != VA_STATUS_SUCCESS) {
      fprintf(stderr, "vaSyncSurface surface=%u failed: %d (%s)\n",
              surface, status, vaErrorStr(status));
      return 1;
   }

   VADRMPRIMESurfaceDescriptor descriptor;
   memset(&descriptor, 0, sizeof(descriptor));
   for (unsigned int i = 0; i < 4; i++)
      descriptor.objects[i].fd = -1;
   status = vaExportSurfaceHandle(display, surface,
                                  VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2,
                                  VA_EXPORT_SURFACE_READ_ONLY |
                                  VA_EXPORT_SURFACE_SEPARATE_LAYERS,
                                  &descriptor);
   printf("DECODED_SURFACE surface=%u frame=%dx%d interlaced=%d top_field_first=%d\n",
          surface, frame->width, frame->height,
          !!(frame->flags & AV_FRAME_FLAG_INTERLACED),
          !!(frame->flags & AV_FRAME_FLAG_TOP_FIELD_FIRST));
   printf("EXPORT status=%d status_name=%s\n", status, vaErrorStr(status));
   if (status != VA_STATUS_SUCCESS)
      return 1;

   printf("DESCRIPTOR fourcc=%#x width=%u height=%u objects=%u layers=%u\n",
          descriptor.fourcc, descriptor.width, descriptor.height,
          descriptor.num_objects, descriptor.num_layers);
   for (uint32_t object = 0; object < descriptor.num_objects; object++) {
      printf("OBJECT index=%u fd=%d size=%" PRIu32 " modifier=%#" PRIx64 "\n",
             object, descriptor.objects[object].fd,
             descriptor.objects[object].size,
             descriptor.objects[object].drm_format_modifier);
   }
   for (uint32_t layer = 0; layer < descriptor.num_layers; layer++) {
      const __typeof__(descriptor.layers[0]) *item = &descriptor.layers[layer];
      printf("LAYER index=%u drm_format=%#x planes=%u\n",
             layer, item->drm_format, item->num_planes);
      for (uint32_t plane = 0; plane < item->num_planes; plane++) {
         printf("PLANE layer=%u index=%u object=%u offset=%u pitch=%u\n",
                layer, plane, item->object_index[plane], item->offset[plane],
                item->pitch[plane]);
      }
   }
   for (uint32_t object = 0; object < descriptor.num_objects; object++) {
      if (descriptor.objects[object].fd >= 0)
         close(descriptor.objects[object].fd);
   }
   return 0;
}

static int
decode_first_vaapi_surface(const char *input, const char *device,
                           const char *expected_driver_path)
{
   AVFormatContext *format = NULL;
   AVCodecContext *decoder = NULL;
   AVBufferRef *device_ref = NULL;
   AVPacket *packet = NULL;
   AVFrame *frame = NULL;
   int result = 1;
   int stream_index = -1;
   VAConfigID query_config = VA_INVALID_ID;

   if (avformat_open_input(&format, input, NULL, NULL) < 0 ||
       avformat_find_stream_info(format, NULL) < 0) {
      fprintf(stderr, "cannot open/probe input %s\n", input);
      goto done;
   }
   for (unsigned int i = 0; i < format->nb_streams; i++) {
      if (format->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
         stream_index = (int)i;
         break;
      }
   }
   if (stream_index < 0 ||
       format->streams[stream_index]->codecpar->codec_id != AV_CODEC_ID_H264) {
      fprintf(stderr, "input does not have the expected H264 video stream\n");
      goto done;
   }

   const AVCodec *codec = avcodec_find_decoder(AV_CODEC_ID_H264);
   if (!codec || find_vaapi_hw_format(codec) < 0) {
      fprintf(stderr, "H264 decoder has no VAAPI hardware configuration\n");
      goto done;
   }
   if (av_hwdevice_ctx_create(&device_ref, AV_HWDEVICE_TYPE_VAAPI,
                              device, NULL, 0) < 0) {
      fprintf(stderr, "cannot create VAAPI device for %s\n", device);
      goto done;
   }
   AVHWDeviceContext *device_context = (AVHWDeviceContext *)device_ref->data;
   AVVAAPIDeviceContext *va_context = device_context->hwctx;
   printf("VA_VENDOR %s\n", vaQueryVendorString(va_context->display));
   if (verify_va_driver_mapped(expected_driver_path) != 0)
      goto done;
   if (query_surface_contract(va_context->display, &query_config) != 0)
      goto done;

   decoder = avcodec_alloc_context3(codec);
   if (!decoder ||
       avcodec_parameters_to_context(
          decoder, format->streams[stream_index]->codecpar) < 0) {
      fprintf(stderr, "cannot allocate/configure H264 decoder\n");
      goto done;
   }
   decoder->get_format = select_vaapi_format;
   decoder->hw_device_ctx = av_buffer_ref(device_ref);
   decoder->pkt_timebase = format->streams[stream_index]->time_base;
   decoder->thread_count = 1;
   if (!decoder->hw_device_ctx || avcodec_open2(decoder, codec, NULL) < 0) {
      fprintf(stderr, "cannot open VAAPI H264 decoder\n");
      goto done;
   }

   packet = av_packet_alloc();
   frame = av_frame_alloc();
   if (!packet || !frame) {
      fprintf(stderr, "cannot allocate FFmpeg packet/frame\n");
      goto done;
   }

   while (av_read_frame(format, packet) >= 0) {
      if (packet->stream_index == stream_index) {
         if (avcodec_send_packet(decoder, packet) < 0) {
            av_packet_unref(packet);
            continue;
         }
         for (;;) {
            int receive = avcodec_receive_frame(decoder, frame);
            if (receive == AVERROR(EAGAIN) || receive == AVERROR_EOF)
               break;
            if (receive < 0) {
               fprintf(stderr, "VAAPI decode failed: %s\n", av_err2str(receive));
               av_packet_unref(packet);
               goto done;
            }
            if (frame->format == AV_PIX_FMT_VAAPI && frame->data[3]) {
               VASurfaceID surface = (VASurfaceID)(uintptr_t)frame->data[3];
               result = try_export_surface(va_context->display,
                                           surface, frame);
               av_frame_unref(frame);
               av_packet_unref(packet);
               goto done;
            }
            av_frame_unref(frame);
         }
      }
      av_packet_unref(packet);
   }
   fprintf(stderr, "decoder produced no VAAPI surface before end of input\n");

done:
   if (query_config != VA_INVALID_ID && device_ref) {
      AVHWDeviceContext *device_context = (AVHWDeviceContext *)device_ref->data;
      AVVAAPIDeviceContext *va_context = device_context->hwctx;
      vaDestroyConfig(va_context->display, query_config);
   }
   av_frame_free(&frame);
   av_packet_free(&packet);
   avcodec_free_context(&decoder);
   av_buffer_unref(&device_ref);
   avformat_close_input(&format);
   return result;
}

int
main(int argc, char **argv)
{
   char resolved_driver[PATH_MAX];
   if (argc != 6 || strcmp(argv[1], "--execute")) {
      fprintf(stderr,
              "usage: %s --execute INPUT /dev/dri/renderD128 "
              "LIBVA_DRIVER_DIR EXPECTED_DRIVER_DSO\n",
              argv[0]);
      return 2;
   }
   if (verify_input_sha256(argv[2]))
      return 2;
   if (prepare_va_driver(argv[4], argv[5], resolved_driver))
      return 2;
   return decode_first_vaapi_surface(argv[2], argv[3], resolved_driver);
}
