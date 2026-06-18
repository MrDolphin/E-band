#include <arpa/inet.h>
#include <errno.h>
#include <iio.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define UDP_PORT 8080
#define MAX_UDP_PACKET 2048
#define PROTOCOL_HEADER_SIZE 17
#define IQ_CONTENT_HEADER_SIZE 12
#define SAMPLE_FORMAT_INT16_IQ 1
#define IQ_BYTES_PER_SAMPLE 4
#define TX_MAX_SAMPLES 65536
#define TX_CHUNK_PAYLOAD_BYTES 1000
#define TX_QUEUE_LIMIT 24
#define VIDEO_IQ_PACKET_TYPE 0x05
#define STOP_PACKET_TYPE 0x04
#define RX_PACKET_TYPE 0x03
#define RX_FRAME_SAMPLES 6144
#define RX_CHUNK_SAMPLES 350
#define SAMPLE_RATE_HZ 30720000LL
#define RF_BANDWIDTH_HZ 28000000LL
#define RF_LO_HZ 200000000LL

static volatile int stop;
static volatile int streaming_active;
static volatile int tx_reset_requested;
static int udp_socket = -1;
static pthread_mutex_t peer_lock = PTHREAD_MUTEX_INITIALIZER;
static struct sockaddr_in pc_peer;
static int pc_peer_valid;

struct tx_assembly {
	uint32_t frame_id;
	uint16_t chunk_count;
	uint16_t received_count;
	uint8_t *data;
	uint8_t *received;
	size_t total_bytes;
};

struct tx_frame {
	uint32_t frame_id;
	size_t sample_count;
	uint8_t *iq;
	struct tx_frame *next;
};

struct tx_queue {
	pthread_mutex_t lock;
	pthread_cond_t ready;
	struct tx_frame *head;
	struct tx_frame *tail;
	size_t count;
};

struct tx_thread_args {
	struct iio_device *device;
	struct iio_channel *i_channel;
	struct iio_channel *q_channel;
	struct tx_queue *queue;
};

struct rx_thread_args {
	struct iio_device *device;
	struct iio_channel *i_channel;
	struct iio_channel *q_channel;
};

static struct tx_queue tx_queue = {
	.lock = PTHREAD_MUTEX_INITIALIZER,
	.ready = PTHREAD_COND_INITIALIZER,
};

static void handle_signal(int sig)
{
	(void)sig;
	stop = 1;
	pthread_cond_broadcast(&tx_queue.ready);
}

static uint16_t read_le16(const uint8_t *p)
{
	return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_le32(const uint8_t *p)
{
	return (uint32_t)p[0] |
		((uint32_t)p[1] << 8) |
		((uint32_t)p[2] << 16) |
		((uint32_t)p[3] << 24);
}

static void write_le16(uint8_t *p, uint16_t value)
{
	p[0] = (uint8_t)value;
	p[1] = (uint8_t)(value >> 8);
}

static void write_le32(uint8_t *p, uint32_t value)
{
	p[0] = (uint8_t)value;
	p[1] = (uint8_t)(value >> 8);
	p[2] = (uint8_t)(value >> 16);
	p[3] = (uint8_t)(value >> 24);
}

static int is_remotevideo_packet(const uint8_t *data, int length)
{
	return length >= PROTOCOL_HEADER_SIZE &&
		data[0] == 0x01 &&
		data[1] == 0xdc &&
		data[2] == 0xef &&
		data[3] == 0x18;
}

static int write_attr(struct iio_channel *channel, const char *name, long long value)
{
	int result = iio_channel_attr_write_longlong(channel, name, value);
	if (result < 0) {
		fprintf(stderr, "failed to write %s=%lld: %d\n", name, value, result);
	}
	return result;
}

static int write_attr_double(struct iio_channel *channel, const char *name, double value)
{
	int result = iio_channel_attr_write_double(channel, name, value);
	if (result < 0) {
		fprintf(stderr, "failed to write %s=%.2f: %d\n", name, value, result);
	}
	return result;
}

static int setup_ad9361(struct iio_context *ctx)
{
	struct iio_device *phy = iio_context_find_device(ctx, "ad9361-phy");
	struct iio_channel *rx_lo;
	struct iio_channel *tx_lo;
	struct iio_channel *rx0;
	struct iio_channel *tx0;

	if (!phy) {
		fprintf(stderr, "missing ad9361-phy\n");
		return -1;
	}
	rx_lo = iio_device_find_channel(phy, "altvoltage0", true);
	tx_lo = iio_device_find_channel(phy, "altvoltage1", true);
	rx0 = iio_device_find_channel(phy, "voltage0", false);
	tx0 = iio_device_find_channel(phy, "voltage0", true);
	if (!rx_lo || !tx_lo || !rx0 || !tx0) {
		fprintf(stderr, "missing AD9361 control channels\n");
		return -1;
	}

	if (write_attr(rx_lo, "frequency", RF_LO_HZ) < 0 ||
		write_attr(tx_lo, "frequency", RF_LO_HZ) < 0 ||
		write_attr(rx0, "sampling_frequency", SAMPLE_RATE_HZ) < 0 ||
		write_attr(tx0, "sampling_frequency", SAMPLE_RATE_HZ) < 0 ||
		write_attr(rx0, "rf_bandwidth", RF_BANDWIDTH_HZ) < 0 ||
		write_attr(tx0, "rf_bandwidth", RF_BANDWIDTH_HZ) < 0 ||
		write_attr_double(tx0, "hardwaregain", -30.5) < 0) {
		return -1;
	}
	if (iio_channel_attr_write(rx0, "gain_control_mode", "manual") < 0 ||
		write_attr_double(rx0, "hardwaregain", 0.0) < 0) {
		fprintf(stderr, "failed to set deterministic RX gain\n");
		return -1;
	}
	return 0;
}

static void reset_assembly(struct tx_assembly *assembly, uint32_t frame_id, uint16_t chunk_count)
{
	free(assembly->data);
	free(assembly->received);
	memset(assembly, 0, sizeof(*assembly));
	assembly->frame_id = frame_id;
	assembly->chunk_count = chunk_count;
	assembly->data = calloc(TX_MAX_SAMPLES, IQ_BYTES_PER_SAMPLE);
	assembly->received = calloc(chunk_count, 1);
	if (!assembly->data || !assembly->received) {
		fprintf(stderr, "failed to allocate TX assembly\n");
		free(assembly->data);
		free(assembly->received);
		memset(assembly, 0, sizeof(*assembly));
	}
}

static void free_tx_frame(struct tx_frame *frame)
{
	if (!frame) {
		return;
	}
	free(frame->iq);
	free(frame);
}

static void clear_tx_queue(struct tx_queue *queue)
{
	struct tx_frame *frame;
	pthread_mutex_lock(&queue->lock);
	frame = queue->head;
	queue->head = NULL;
	queue->tail = NULL;
	queue->count = 0;
	pthread_mutex_unlock(&queue->lock);
	while (frame) {
		struct tx_frame *next = frame->next;
		free_tx_frame(frame);
		frame = next;
	}
}

static int enqueue_tx(
	struct tx_queue *queue,
	uint32_t frame_id,
	const uint8_t *iq,
	size_t sample_count)
{
	struct tx_frame *frame;
	size_t byte_count = sample_count * IQ_BYTES_PER_SAMPLE;

	frame = calloc(1, sizeof(*frame));
	if (!frame) {
		return -1;
	}
	frame->iq = malloc(byte_count);
	if (!frame->iq) {
		free(frame);
		return -1;
	}
	memcpy(frame->iq, iq, byte_count);
	frame->frame_id = frame_id;
	frame->sample_count = sample_count;

	pthread_mutex_lock(&queue->lock);
	if (queue->count >= TX_QUEUE_LIMIT) {
		pthread_mutex_unlock(&queue->lock);
		fprintf(stderr, "TX queue full, dropping frame=%u\n", frame_id);
		free_tx_frame(frame);
		return -1;
	}
	if (queue->tail) {
		queue->tail->next = frame;
	} else {
		queue->head = frame;
	}
	queue->tail = frame;
	queue->count++;
	pthread_cond_signal(&queue->ready);
	pthread_mutex_unlock(&queue->lock);
	return 0;
}

static struct tx_frame *dequeue_tx(struct tx_queue *queue)
{
	struct tx_frame *frame;
	pthread_mutex_lock(&queue->lock);
	while (!stop && !tx_reset_requested && !queue->head) {
		pthread_cond_wait(&queue->ready, &queue->lock);
	}
	frame = queue->head;
	if (frame) {
		queue->head = frame->next;
		if (!queue->head) {
			queue->tail = NULL;
		}
		queue->count--;
		frame->next = NULL;
	}
	pthread_mutex_unlock(&queue->lock);
	return frame;
}

static int push_tx_frame(
	struct iio_device *device,
	struct iio_channel *i_channel,
	struct iio_channel *q_channel,
	struct iio_buffer **active_buffer,
	size_t *active_sample_count,
	bool *active_cyclic,
	const uint8_t *iq,
	size_t sample_count,
	bool cyclic)
{
	struct iio_buffer *buffer = *active_buffer;
	const struct iio_data_format *format;
	ptrdiff_t step;
	char *i_ptr;
	char *q_ptr;
	char *end;
	size_t index;
	ssize_t pushed;
	unsigned int shift;

	if (cyclic || !buffer || *active_sample_count != sample_count ||
		*active_cyclic != cyclic) {
		if (buffer) {
			iio_buffer_destroy(buffer);
		}
		buffer = iio_device_create_buffer(device, sample_count, cyclic);
		if (!buffer) {
			*active_buffer = NULL;
			*active_sample_count = 0;
			*active_cyclic = false;
			fprintf(stderr, "failed to create TX buffer for %zu samples\n",
				sample_count);
			return -1;
		}
		*active_buffer = buffer;
		*active_sample_count = sample_count;
		*active_cyclic = cyclic;
		printf("TX buffer prepared: samples=%zu duration=%.3f ms cyclic=%d\n",
			sample_count,
			sample_count * 1000.0 / (double)SAMPLE_RATE_HZ,
			cyclic ? 1 : 0);
	}
	format = iio_channel_get_data_format(i_channel);
	shift = format ? format->shift : 0;
	step = iio_buffer_step(buffer);
	i_ptr = iio_buffer_first(buffer, i_channel);
	q_ptr = iio_buffer_first(buffer, q_channel);
	end = iio_buffer_end(buffer);
	for (index = 0;
		index < sample_count && i_ptr < end && q_ptr < end;
		index++, i_ptr += step, q_ptr += step) {
		int16_t source_i;
		int16_t source_q;
		int16_t packed_i;
		int16_t packed_q;
		memcpy(&source_i, iq + index * IQ_BYTES_PER_SAMPLE, sizeof(source_i));
		memcpy(&source_q, iq + index * IQ_BYTES_PER_SAMPLE + 2, sizeof(source_q));
		packed_i = (int16_t)(source_i * (int)(1U << shift));
		packed_q = (int16_t)(source_q * (int)(1U << shift));
		memcpy(i_ptr, &packed_i, sizeof(packed_i));
		memcpy(q_ptr, &packed_q, sizeof(packed_q));
	}
	pushed = iio_buffer_push(buffer);
	if (pushed < 0) {
		fprintf(stderr, "non-cyclic TX push failed: %zd\n", pushed);
		return -1;
	}
	return 0;
}

static void *tx_thread(void *opaque)
{
	struct tx_thread_args *args = opaque;
	struct iio_buffer *buffer = NULL;
	size_t buffer_sample_count = 0;
	bool buffer_cyclic = false;
	uint32_t sent_count = 0;
	while (!stop) {
		struct tx_frame *frame = dequeue_tx(args->queue);
		if (tx_reset_requested) {
			if (buffer) {
				iio_buffer_destroy(buffer);
				buffer = NULL;
				buffer_sample_count = 0;
				buffer_cyclic = false;
				printf("TX buffer stopped\n");
			}
			tx_reset_requested = 0;
		}
		if (!frame) {
			continue;
		}
		if (streaming_active &&
			push_tx_frame(
				args->device,
				args->i_channel,
				args->q_channel,
				&buffer,
				&buffer_sample_count,
				&buffer_cyclic,
				frame->iq,
				frame->sample_count,
				frame->sample_count == RX_FRAME_SAMPLES) == 0) {
			sent_count++;
			if (sent_count % 100 == 0 ||
				frame->sample_count == RX_FRAME_SAMPLES) {
				printf("video TX sent: frames=%u last=%u samples=%zu cyclic=%d\n",
					sent_count,
					frame->frame_id,
					frame->sample_count,
					frame->sample_count == RX_FRAME_SAMPLES ? 1 : 0);
			}
		}
		free_tx_frame(frame);
	}
	if (buffer) {
		iio_buffer_destroy(buffer);
	}
	return NULL;
}

static size_t copy_rx_iq(
	struct iio_buffer *buffer,
	struct iio_channel *i_channel,
	struct iio_channel *q_channel,
	uint8_t *destination,
	size_t maximum_samples)
{
	ptrdiff_t step = iio_buffer_step(buffer);
	char *i_pointer = iio_buffer_first(buffer, i_channel);
	char *q_pointer = iio_buffer_first(buffer, q_channel);
	char *end = iio_buffer_end(buffer);
	size_t count = 0;
	while (i_pointer < end &&
		q_pointer < end &&
		count < maximum_samples) {
		memcpy(
			destination + count * IQ_BYTES_PER_SAMPLE,
			i_pointer,
			sizeof(int16_t));
		memcpy(
			destination + count * IQ_BYTES_PER_SAMPLE + sizeof(int16_t),
			q_pointer,
			sizeof(int16_t));
		i_pointer += step;
		q_pointer += step;
		count++;
	}
	return count;
}

static int send_rx_chunk(
	uint32_t frame_id,
	uint16_t chunk_index,
	uint16_t chunk_count,
	const uint8_t *iq,
	uint16_t sample_count)
{
	uint8_t packet[PROTOCOL_HEADER_SIZE + IQ_CONTENT_HEADER_SIZE +
		RX_CHUNK_SAMPLES * IQ_BYTES_PER_SAMPLE];
	uint16_t payload_bytes = sample_count * IQ_BYTES_PER_SAMPLE;
	uint16_t content_length = IQ_CONTENT_HEADER_SIZE + payload_bytes;
	struct sockaddr_in peer;
	int valid;

	memset(packet, 0, sizeof(packet));
	packet[0] = 0x01;
	packet[1] = 0xdc;
	packet[2] = 0xef;
	packet[3] = 0x18;
	packet[4] = RX_PACKET_TYPE;
	packet[5] = 1;
	write_le16(packet + 15, content_length);
	write_le32(packet + 17, frame_id);
	write_le16(packet + 21, chunk_index);
	write_le16(packet + 23, chunk_count);
	write_le16(packet + 25, SAMPLE_FORMAT_INT16_IQ);
	write_le16(packet + 27, sample_count);
	memcpy(packet + 29, iq, payload_bytes);

	pthread_mutex_lock(&peer_lock);
	valid = pc_peer_valid;
	peer = pc_peer;
	pthread_mutex_unlock(&peer_lock);
	if (!valid) {
		return 0;
	}
	return sendto(
		udp_socket,
		packet,
		PROTOCOL_HEADER_SIZE + content_length,
		0,
		(struct sockaddr *)&peer,
		sizeof(peer)) < 0 ? -1 : 0;
}

static void *rx_thread(void *opaque)
{
	struct rx_thread_args *args = opaque;
	struct iio_buffer *buffer;
	uint8_t *iq;
	uint32_t frame_id = 0;

	buffer = iio_device_create_buffer(args->device, RX_FRAME_SAMPLES, false);
	if (!buffer) {
		fprintf(stderr, "failed to create RX buffer\n");
		return NULL;
	}
	iq = malloc(RX_FRAME_SAMPLES * IQ_BYTES_PER_SAMPLE);
	if (!iq) {
		iio_buffer_destroy(buffer);
		return NULL;
	}

	while (!stop) {
		ssize_t refilled;
		size_t sample_count;
		uint16_t chunk_count;
		uint16_t chunk_index;

		if (!streaming_active || !pc_peer_valid) {
			usleep(10000);
			continue;
		}
		refilled = iio_buffer_refill(buffer);
		if (refilled < 0) {
			fprintf(stderr, "RX refill failed: %zd\n", refilled);
			continue;
		}
		sample_count = copy_rx_iq(
			buffer,
			args->i_channel,
			args->q_channel,
			iq,
			RX_FRAME_SAMPLES);
		chunk_count = (uint16_t)((sample_count + RX_CHUNK_SAMPLES - 1) /
			RX_CHUNK_SAMPLES);
		for (chunk_index = 0; chunk_index < chunk_count; chunk_index++) {
			size_t offset = chunk_index * RX_CHUNK_SAMPLES;
			uint16_t count = (uint16_t)(sample_count - offset);
			if (count > RX_CHUNK_SAMPLES) {
				count = RX_CHUNK_SAMPLES;
			}
			if (send_rx_chunk(
				frame_id,
				chunk_index,
				chunk_count,
				iq + offset * IQ_BYTES_PER_SAMPLE,
				count) < 0) {
				perror("send RX IQ");
				break;
			}
		}
		if (frame_id % 1000 == 0) {
			printf("video RX returned: frame=%u samples=%zu chunks=%u\n",
				frame_id,
				sample_count,
				chunk_count);
		}
		frame_id++;
	}
	free(iq);
	iio_buffer_destroy(buffer);
	return NULL;
}

int main(void)
{
	struct iio_context *ctx;
	struct iio_device *tx_device;
	struct iio_device *rx_device;
	struct iio_channel *tx_i;
	struct iio_channel *tx_q;
	struct iio_channel *rx_i;
	struct iio_channel *rx_q;
	struct sockaddr_in address;
	struct tx_assembly assembly = {0};
	struct tx_thread_args tx_args;
	struct rx_thread_args rx_args;
	pthread_t tx_worker;
	pthread_t rx_worker;
	int tx_started = 0;
	int rx_started = 0;
	uint8_t packet[MAX_UDP_PACKET];

	setvbuf(stdout, NULL, _IOLBF, 0);
	setvbuf(stderr, NULL, _IONBF, 0);
	signal(SIGINT, handle_signal);
	signal(SIGTERM, handle_signal);

	ctx = iio_create_local_context();
	if (!ctx || setup_ad9361(ctx) != 0) {
		fprintf(stderr, "failed to initialize AD9361\n");
		return 1;
	}
	tx_device = iio_context_find_device(ctx, "cf-ad9361-dds-core-lpc");
	rx_device = iio_context_find_device(ctx, "cf-ad9361-lpc");
	if (!tx_device || !rx_device) {
		fprintf(stderr, "missing AD9361 streaming devices\n");
		return 1;
	}
	tx_i = iio_device_find_channel(tx_device, "voltage0", true);
	tx_q = iio_device_find_channel(tx_device, "voltage1", true);
	rx_i = iio_device_find_channel(rx_device, "voltage0", false);
	rx_q = iio_device_find_channel(rx_device, "voltage1", false);
	if (!tx_i || !tx_q || !rx_i || !rx_q) {
		fprintf(stderr, "missing streaming channels\n");
		return 1;
	}
	iio_channel_enable(tx_i);
	iio_channel_enable(tx_q);
	iio_channel_enable(rx_i);
	iio_channel_enable(rx_q);

	udp_socket = socket(AF_INET, SOCK_DGRAM, 0);
	if (udp_socket < 0) {
		perror("socket");
		return 1;
	}
	{
		int send_buffer = 4 * 1024 * 1024;
		setsockopt(
			udp_socket,
			SOL_SOCKET,
			SO_SNDBUF,
			&send_buffer,
			sizeof(send_buffer));
	}
	memset(&address, 0, sizeof(address));
	address.sin_family = AF_INET;
	address.sin_addr.s_addr = htonl(INADDR_ANY);
	address.sin_port = htons(UDP_PORT);
	if (bind(udp_socket, (struct sockaddr *)&address, sizeof(address)) < 0) {
		perror("bind");
		return 1;
	}

	tx_args.device = tx_device;
	tx_args.i_channel = tx_i;
	tx_args.q_channel = tx_q;
	tx_args.queue = &tx_queue;
	rx_args.device = rx_device;
	rx_args.i_channel = rx_i;
	rx_args.q_channel = rx_q;
	tx_started = pthread_create(&tx_worker, NULL, tx_thread, &tx_args) == 0;
	rx_started = pthread_create(&rx_worker, NULL, rx_thread, &rx_args) == 0;

	printf("E310 video modem bridge listening on UDP %d\n", UDP_PORT);
	printf("LO=200 MHz Fs=30.72 MSPS BW=28 MHz TX1/RX1 FMCW IQ stream RX_FRAME=%d\n",
		RX_FRAME_SAMPLES);
	printf("TX attenuation=-30.5 dB, RX manual gain=0 dB\n");

	while (!stop) {
		struct sockaddr_in peer;
		socklen_t peer_length = sizeof(peer);
		ssize_t length = recvfrom(
			udp_socket,
			packet,
			sizeof(packet),
			0,
			(struct sockaddr *)&peer,
			&peer_length);
		uint8_t packet_type;
		uint16_t content_length;
		const uint8_t *content;
		uint32_t frame_id;
		uint16_t chunk_index;
		uint16_t chunk_count;
		uint16_t sample_format;
		uint16_t sample_count;
		uint16_t iq_bytes;
		size_t offset;

		if (length < 0) {
			if (errno == EINTR) {
				continue;
			}
			perror("recvfrom");
			break;
		}
		if (!is_remotevideo_packet(packet, (int)length)) {
			continue;
		}
		pthread_mutex_lock(&peer_lock);
		pc_peer = peer;
		pc_peer_valid = 1;
		pthread_mutex_unlock(&peer_lock);

		packet_type = packet[4];
		if (packet_type == STOP_PACKET_TYPE) {
			streaming_active = 0;
			tx_reset_requested = 1;
			clear_tx_queue(&tx_queue);
			pthread_cond_broadcast(&tx_queue.ready);
			printf("video streaming stopped by %s:%u\n",
				inet_ntoa(peer.sin_addr),
				ntohs(peer.sin_port));
			continue;
		}
		content_length = read_le16(packet + 15);
		if (packet_type != VIDEO_IQ_PACKET_TYPE ||
			PROTOCOL_HEADER_SIZE + content_length > length ||
			content_length < IQ_CONTENT_HEADER_SIZE) {
			continue;
		}

		content = packet + PROTOCOL_HEADER_SIZE;
		frame_id = read_le32(content);
		chunk_index = read_le16(content + 4);
		chunk_count = read_le16(content + 6);
		sample_format = read_le16(content + 8);
		sample_count = read_le16(content + 10);
		iq_bytes = content_length - IQ_CONTENT_HEADER_SIZE;
		if (sample_format != SAMPLE_FORMAT_INT16_IQ ||
			sample_count * IQ_BYTES_PER_SAMPLE > iq_bytes ||
			chunk_count == 0 ||
			chunk_index >= chunk_count) {
			continue;
		}
		if (!assembly.data ||
			assembly.frame_id != frame_id ||
			assembly.chunk_count != chunk_count) {
			reset_assembly(&assembly, frame_id, chunk_count);
		}
		if (!assembly.data) {
			continue;
		}
		offset = (size_t)chunk_index * TX_CHUNK_PAYLOAD_BYTES;
		if (offset + iq_bytes > TX_MAX_SAMPLES * IQ_BYTES_PER_SAMPLE) {
			fprintf(stderr, "video TX frame too large\n");
			continue;
		}
		memcpy(assembly.data + offset, content + IQ_CONTENT_HEADER_SIZE, iq_bytes);
		if (!assembly.received[chunk_index]) {
			assembly.received[chunk_index] = 1;
			assembly.received_count++;
		}
		if (offset + iq_bytes > assembly.total_bytes) {
			assembly.total_bytes = offset + iq_bytes;
		}
		if (assembly.received_count == assembly.chunk_count) {
			size_t complete_samples = assembly.total_bytes / IQ_BYTES_PER_SAMPLE;
			streaming_active = 1;
			if (enqueue_tx(
				&tx_queue,
				assembly.frame_id,
				assembly.data,
				complete_samples) == 0 &&
				assembly.frame_id % 100 == 0) {
				printf("video IQ queued: frame=%u samples=%zu\n",
					assembly.frame_id,
					complete_samples);
			}
			assembly.received_count = 0;
			memset(assembly.received, 0, assembly.chunk_count);
		}
	}

	stop = 1;
	pthread_cond_broadcast(&tx_queue.ready);
	if (tx_started) {
		pthread_join(tx_worker, NULL);
	}
	if (rx_started) {
		pthread_cancel(rx_worker);
		pthread_join(rx_worker, NULL);
	}
	clear_tx_queue(&tx_queue);
	free(assembly.data);
	free(assembly.received);
	close(udp_socket);
	iio_context_destroy(ctx);
	return 0;
}
