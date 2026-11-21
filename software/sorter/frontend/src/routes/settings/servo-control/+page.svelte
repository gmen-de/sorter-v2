<script lang="ts">
	import ServoBackendSection from '$lib/components/settings/ServoBackendSection.svelte';
	import Esp32FirmwareSection from '$lib/components/settings/Esp32FirmwareSection.svelte';
	import ServoGateDiagPanel from '$lib/components/settings/ServoGateDiagPanel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Tabs from '$lib/components/ui/Tabs.svelte';

	type Tab = 'settings' | 'diagnostics';
	let tab = $state<Tab>('settings');
</script>

<svelte:head><title>Sorter - Servo control</title></svelte:head>

<PageHeader
	title="Servo control"
	description="Where layer-gate servo commands go: a control board's PCA9685 channels, or the ESP32 BLE gate bridge."
/>

<Tabs
	label="Servo control sections"
	bind:value={tab}
	items={[
		{ value: 'settings', label: 'Settings' },
		{ value: 'diagnostics', label: 'Diagnostics' }
	]}
/>

{#if tab === 'settings'}
	<div class="flex flex-col gap-4">
		<ServoBackendSection />
		<Esp32FirmwareSection />
	</div>
{:else}
	<div class="flex flex-col gap-4">
		<ServoGateDiagPanel />
	</div>
{/if}
