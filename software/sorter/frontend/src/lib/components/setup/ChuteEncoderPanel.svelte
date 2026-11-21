<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';

	let {
		connected,
		encoderCount,
		indexSynced,
		homed,
		countsPerRevolution = $bindable(),
		homing,
		canceling,
		error,
		status,
		onFindIndex,
		onCancel,
		onSaveCountsPerRevolution
	}: {
		connected: boolean;
		encoderCount: number | null;
		indexSynced: boolean | null;
		homed: boolean | null;
		countsPerRevolution: number;
		homing: boolean;
		canceling: boolean;
		error: string | null;
		status: string | null;
		onFindIndex: () => void;
		onCancel: () => void;
		onSaveCountsPerRevolution: (value: number) => void;
	} = $props();
</script>

<Panel
	title={$_('components.setup.chuteEncoderPanel.panelTitle')}
	description={$_('components.setup.chuteEncoderPanel.panelDescription')}
>
	{#snippet actions()}
		<Badge tone={connected ? 'success' : 'neutral'} dot>
			{connected
				? $_('components.setup.chuteEncoderPanel.badge.connected')
				: $_('components.setup.chuteEncoderPanel.badge.notConnected')}
		</Badge>
	{/snippet}

	<div class="flex flex-col gap-4">
		<Alert tone="warning">
			{$_('components.setup.chuteEncoderPanel.warningBeforeCode')}
			<code class="font-mono text-xs">software/firmware/octopus_control_board/ChuteEncoder.h</code>
			{$_('components.setup.chuteEncoderPanel.warningAfterCode')}
		</Alert>

		<p class="text-sm text-ink-muted">
			{$_('components.setup.chuteEncoderPanel.findIndexExplanation')}
		</p>

		<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
			<div class="flex items-center justify-between rounded-control bg-well px-4 py-3">
				<span class="text-sm text-ink-muted">{$_('components.setup.chuteEncoderPanel.stats.indexSynced')}</span>
				<Badge tone={indexSynced ? 'success' : 'neutral'}>
					{indexSynced === null
						? '–'
						: indexSynced
							? $_('components.setup.chuteEncoderPanel.status.yes')
							: $_('components.setup.chuteEncoderPanel.status.notYet')}
				</Badge>
			</div>
			<div class="flex items-center justify-between rounded-control bg-well px-4 py-3">
				<span class="text-sm text-ink-muted">{$_('components.setup.chuteEncoderPanel.stats.homed')}</span>
				<Badge tone={homed ? 'success' : 'neutral'}>
					{homed === null
						? '–'
						: homed
							? $_('components.setup.chuteEncoderPanel.status.yes')
							: $_('components.setup.chuteEncoderPanel.status.no')}
				</Badge>
			</div>
			<div class="flex items-center justify-between rounded-control bg-well px-4 py-3">
				<span class="text-sm text-ink-muted">{$_('components.setup.chuteEncoderPanel.stats.rawCount')}</span>
				<span class="font-mono text-sm text-ink">{encoderCount ?? '–'}</span>
			</div>
		</div>

		<Field
			label={$_('components.setup.chuteEncoderPanel.countsPerRevolution.label')}
			for="chute-encoder-counts-per-rev"
			help={$_('components.setup.chuteEncoderPanel.countsPerRevolution.help')}
		>
			<Input
				id="chute-encoder-counts-per-rev"
				type="number"
				step={1}
				min={1}
				bind:value={countsPerRevolution}
				onchange={() => onSaveCountsPerRevolution(countsPerRevolution)}
			/>
		</Field>

		{#if error}
			<Alert tone="danger">{error}</Alert>
		{:else if status}
			<Alert tone="success">{status}</Alert>
		{/if}
	</div>

	{#snippet footer()}
		<Button variant="ghost" loading={canceling} onclick={onCancel}
			>{$_('components.setup.chuteEncoderPanel.buttons.stopMotion')}</Button
		>
		<Button variant="primary" loading={homing} disabled={!connected} onclick={onFindIndex}>
			{$_('components.setup.chuteEncoderPanel.buttons.findIndex')}
		</Button>
	{/snippet}
</Panel>
