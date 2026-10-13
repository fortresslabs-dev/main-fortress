import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import SdnSearch from '../SdnSearch'

vi.mock('../../services/api')

describe('<SdnSearch />', () => {
  it('renders the SDN screening header', async () => {
    renderWithRouter(<SdnSearch />)
    expect(await screen.findByRole('heading', { name: /SDN Screening/i })).toBeInTheDocument()
  })
})
