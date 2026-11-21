<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	type WavesharePort = {
		device: string;
		product: string;
		serial: string | null;
		confirmed?: boolean;
		servo_count?: number;
	};

	let {
		port = $bindable(),
		availablePorts,
		loadingPorts,
		onLoadPorts,
		onScan
	}: {
		port: string;
		availablePorts: WavesharePort[];
		loadingPorts: boolean;
		onLoadPorts: () => void;
		onScan: () => void;
	} = $props();
</script>

<Panel title={$_('components.setup.servo.serialPortPanel.panelTitle')}>
	<div class="flex flex-wrap items-center gap-2">
		<Select
			label={$_('components.setup.servo.serialPortPanel.selectLabel')}
			class="min-w-0 flex-[2_1_16rem]"
			bind:value={port}
			options={[
				{ value: '', label: $_('components.setup.servo.serialPortPanel.detectOption') },
				...availablePorts.map((candidate) => ({
					value: candidate.device,
					label: `${candidate.device} · ${candidate.product}`
				}))
			]}
		/>
		<Button loading={loadingPorts} onclick={onLoadPorts}
			>{$_('components.setup.servo.serialPortPanel.refreshPorts')}</Button
		>
		<Button variant="primary" onclick={onScan}
			>{$_('components.setup.servo.serialPortPanel.scanBus')}</Button
		>
	</div>
</Panel>
