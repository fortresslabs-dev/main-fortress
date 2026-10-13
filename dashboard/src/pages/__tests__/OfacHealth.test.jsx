import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import OfacHealth from '../OfacHealth'

vi.mock('../../services/api')

describe('<OfacHealth />', () => {
  it('renders the OFAC / SDN screening header', async () => {
    renderWithRouter(<OfacHealth />)
    expect(await screen.findByRole('heading', { name: /OFAC \/ SDN screening/i })).toBeInTheDocument()
  })
})
