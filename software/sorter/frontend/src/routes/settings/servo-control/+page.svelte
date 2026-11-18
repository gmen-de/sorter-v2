<script lang="ts">
	import ServoBackendSection from '$lib/components/settings/ServoBackendSection.svelte';
	import Esp32FirmwareSection from '$lib/components/settings/Esp32FirmwareSection.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>Sorter - Servo control</title></svelte:head>

<PageHeader
	title="Servo control"
	description="Where layer-gate servo commands go: a control board's PCA9685 channels, or the ESP32 BLE gate bridge."
/>
<div class="flex flex-col gap-4">
	<ServoBackendSection />
	<Esp32FirmwareSection />
</div>
